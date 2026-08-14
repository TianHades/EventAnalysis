"""ImpactMeasurement 节点（量化）：候选 → 度量（spec §4）。"""

from datetime import timedelta
from typing import Any

from ...adapters.quote_adapter import AkshareQuoteAdapter
from ...adapters.stock_master import StockMaster
from ...measurement.event_study import measure_stock
from ...schema import ImpactMeasurement
from ..node import Node


class ImpactMeasurementNode(Node):
    node_id = "impact_measurement"
    output_type = list[ImpactMeasurement]  # SQLite 缓存重建

    def __init__(self, quote_adapter: AkshareQuoteAdapter, stock_master: StockMaster, storage=None, industry_classifier=None, ts_source=None) -> None:
        super().__init__(storage)
        self.qa = quote_adapter
        self.sm = stock_master
        self.classifier = industry_classifier  # Callable[[str], str|None]
        self.ts = ts_source  # TushareSource for 双层基准

    def run(self, inputs: dict[str, Any]) -> list:
        event = inputs["event"]
        candidates = inputs["candidates"]
        bench = inputs["bench_df"]
        episode_id = event.episodes[0].id
        start = event.reaction_date - timedelta(days=350)
        end = event.reaction_date + timedelta(days=200)
        results = []
        for c in candidates:
            code = c["stock_id"]
            resolved = self.sm.resolve(code)
            if resolved is None:
                if self.storage:
                    self.storage.append_skip(replay_id="adhoc", stock_id=code, episode_id=episode_id, reason="resolve 失败")
                continue
            if not self.sm.is_real_at(code, event.reaction_date):
                if self.storage:
                    self.storage.append_skip(replay_id="adhoc", stock_id=code, episode_id=episode_id, reason="事件日不在市（未上市/已退市）")
                continue
            try:
                q = self.qa.daily_adjusted(code, start, end)
            except Exception as e:
                if self.storage:
                    self.storage.append_skip(replay_id="adhoc", stock_id=code, episode_id=episode_id, reason=f"行情拉取失败:{type(e).__name__}")
                continue
            # 双层基准：按标的 L1 行业拉对应行业指数
            bench_ind = None
            if self.ts:
                l1 = self.ts.classify_l1(code)
                if l1:
                    ind_codes = self.ts.industry_index_codes()
                    ic = ind_codes.get(l1)
                    if ic:
                        bench_ind = self.ts.fetch_industry_index_daily(ic)
            m = measure_stock(
                stock_id=code,
                episode_id=episode_id,
                quotes=q,
                bench=bench,
                reaction_date=event.reaction_date,
                board=resolved.board,
                is_st=resolved.is_st,
                listing_date=resolved.listing_date,
                direction=c["direction"],
                bench_industry=bench_ind,
            )
            if m is None:
                if self.storage:
                    self.storage.append_skip(replay_id="adhoc", stock_id=code, episode_id=episode_id, reason="度量返回 None（新股/数据不足）")
                continue
            m.chain_node_id = c.get("chain_node_id")
            m.rationale = c.get("rationale")
            m.stock_name = resolved.name
            m.board = resolved.board
            if resolved.market == "港股":
                m.sw_industry_l3 = resolved.industry  # 港股恒生行业（复用字段）
            elif self.classifier:
                m.sw_industry_l3 = self.classifier(code)
            results.append(m)
        return results
