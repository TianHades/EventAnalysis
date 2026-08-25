"""ReplayOrchestrator：7 节点串联 + 缓存 + 报告落盘。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..adapters.quote_adapter import AkshareQuoteAdapter
from ..adapters.stock_master import StockMaster
from ..adapters.theme_index import ThemeIndex
from ..adapters.tushare_source import TushareSource
from ..llm import Reasoner
from ..schema import Replay
from ..storage import Storage
from .nodes import (
    AttributionNode,
    CandidateNode,
    EventIngestNode,
    ImpactMeasurementNode,
    IndustryChainNode,
    ReportNode,
    TransmissionChainNode,
)


def make_recall_sources() -> tuple[TushareSource, list]:
    """成分股召回源装配（Replay/Forward 编排共用）。

    返回 (tushare_source, sources)：tushare 权威（sources[0]，有缓存就用，
    供 classify/ts_source 复用）+ curated ThemeIndex 兜底。
    """
    ts = TushareSource()
    return ts, [ts, ThemeIndex()]


class ReplayOrchestrator:
    def __init__(
        self,
        reasoner: Reasoner,
        quote_adapter: AkshareQuoteAdapter,
        stock_master: StockMaster,
        storage: Storage | None = None,
        reports_dir: str | Path = "reports",
        market: str = "A股",
    ) -> None:
        self.reasoner = reasoner
        self.qa = quote_adapter
        self.sm = stock_master
        self.storage = storage
        self.reports_dir = Path(reports_dir)
        self.market = market
        self.ingest = EventIngestNode(reasoner, storage)
        self.chain = IndustryChainNode(reasoner, storage)
        # 成分股召回源 + 行业分类器：tushare 权威（sources[0]，有缓存就用）+ curated 兜底
        self._ts, sources = make_recall_sources()
        self.cand = CandidateNode(reasoner, stock_master, storage, sources)
        self.impact = ImpactMeasurementNode(quote_adapter, stock_master, storage, industry_classifier=self._ts.classify, ts_source=self._ts)
        self.attr = AttributionNode(storage)
        self.synth = TransmissionChainNode(reasoner, storage)
        self.report = ReportNode(reasoner, storage)

    def _make_replay_record(self, replay_id: str, event_id: str, status: str, started_at: datetime,
                            finished_at: datetime | None = None, report_path: str | None = None) -> Replay:
        return Replay(
            id=replay_id,
            event_id=event_id,
            pipeline_version="1",
            prompt_hash="",
            model_alias=getattr(self.reasoner, "model_alias", "") or "",
            cli_version="",
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            report_path=report_path,
        )

    def replay(
        self,
        *,
        description: str,
        announcement_date: str,
        reaction_date: str | None = None,
        event_id: str = "evt",
        benchmark: str = "sh000300",
    ) -> tuple[str, Path]:
        # Replay 状态接线：开始 running、成功 done、异常 failed 后 re-raise，
        # 让半成品（C2）在 replays 表里可辨识，而非只在报告缺失时被发现
        started = datetime.utcnow()
        if self.storage is not None:
            self.storage.save_replay(self._make_replay_record(event_id, event_id, "running", started))
        try:
            markdown, path = self._replay_inner(
                description=description,
                announcement_date=announcement_date,
                reaction_date=reaction_date,
                event_id=event_id,
                benchmark=benchmark,
                ctx={"_replay_id": event_id},
            )
        except Exception:
            if self.storage is not None:
                self.storage.save_replay(
                    self._make_replay_record(event_id, event_id, "failed", started, finished_at=datetime.utcnow())
                )
            raise
        if self.storage is not None:
            self.storage.save_replay(
                self._make_replay_record(event_id, event_id, "done", started, finished_at=datetime.utcnow(),
                                         report_path=str(path))
            )
        return markdown, path

    def _replay_inner(
        self,
        *,
        description: str,
        announcement_date: str,
        reaction_date: str | None,
        event_id: str,
        benchmark: str,
        ctx: dict,
    ) -> tuple[str, Path]:
        rd = reaction_date or announcement_date

        event = self.ingest.run_cached(
            {
                "description": description,
                "announcement_date": announcement_date,
                "reaction_date": rd,
                "event_id": event_id,
                **ctx,
            }
        )
        event = event.model_copy(update={"market": self.market})  # 多市场标记（Phase 5）

        # 基准指数一次拉取
        start = event.reaction_date - timedelta(days=350)
        end = event.reaction_date + timedelta(days=200)
        try:
            bench_df = self.qa.index_daily(benchmark, start, end)
        except Exception as e:
            print(f"[warn] 基准指数 {benchmark} 拉取失败: {type(e).__name__}，所有标的度量将返回 None")
            bench_df = pd.DataFrame()

        chain_nodes = self.chain.run_cached({"event": event, **ctx})
        candidates = self.cand.run_cached({"event": event, "chain_nodes": chain_nodes, **ctx})
        measurements = self.impact.run_cached(
            {"event": event, "candidates": candidates, "bench_df": bench_df, **ctx}
        )
        attribution = self.attr.run_cached(
            {"event": event, "candidates": candidates, "measurements": measurements, **ctx}
        )
        # 回写 tier 到 measurements（AttributionNode 入池标的分级，供 report/pattern 用）
        _tier_map = {pm.stock_id: pm.tier for pm in attribution.beneficiary_pool + attribution.victim_pool}
        measurements = [m.model_copy(update={"tier": _tier_map.get(m.stock_id, m.tier)}) for m in measurements]
        chain = self.synth.run_cached(
            {"event": event, "chain_nodes": chain_nodes, "attribution_result": attribution, **ctx}
        )
        # 持久化关键产物
        if self.storage is not None:
            self.storage.save_event(event)
            self.storage.save_chain(chain)
            self.storage.save_attribution(attribution, event_id)
            for m in measurements:
                self.storage.save_measurement(m, event_id)
        markdown = self.report.run_cached(
            {
                "event": event,
                "chain": chain,
                "attribution_result": attribution,
                "measurements": measurements,
                **ctx,
            }
        )

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        from ..measurement.report_template import report_filename
        path = self.reports_dir / f"{report_filename(description, announcement_date)}.md"
        path.write_text(markdown, encoding="utf-8")
        return markdown, path
