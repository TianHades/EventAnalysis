"""ForwardOrchestrator：前瞻映射路由（三层降级）+ 串联节点。"""

from __future__ import annotations

from pathlib import Path

from ..adapters.quote_adapter import AkshareQuoteAdapter
from ..adapters.stock_master import StockMaster
from ..adapters.tushare_source import TushareSource
from ..llm import Reasoner
from ..pipeline.nodes.calibration import InitialCalibrationNode
from ..pipeline.nodes.forward_mapping import ForwardMappingNode
from ..pipeline.nodes.ingest import EventIngestNode
from ..pipeline.nodes.chain import IndustryChainNode
from ..pipeline.nodes.candidate import CandidateNode
from ..pipeline.pattern_service import PatternService
from ..schema import ForwardPrediction, ForwardResult, StockDirection
from ..storage import Storage

# 降级阈值（cosine similarity）：实测同类 0.74-0.81，无关 0.35-0.40
STRONG_THRESHOLD = 0.6
WEAK_THRESHOLD = 0.45  # 0.3 偏低（无关事件 0.35-0.40 误进 weak）→ 0.45


class ForwardOrchestrator:
    """前瞻映射编排：三层降级路由 + 复用 Phase 1/2 组件。"""

    def __init__(
        self,
        reasoner: Reasoner,
        quote_adapter: AkshareQuoteAdapter,
        stock_master: StockMaster,
        pattern_service: PatternService,
        storage: Storage | None = None,
        reports_dir: str | Path = "reports",
        market: str = "A股",
        benchmark: str | None = None,
    ) -> None:
        self.reasoner = reasoner
        self.qa = quote_adapter
        self.sm = stock_master
        self.ps = pattern_service
        self.storage = storage
        self.reports_dir = Path(reports_dir)
        self.market = market
        self.benchmark = benchmark or ("HSI" if market == "港股" else "sh000300")

        # 复用 Phase 1 节点
        self.ingest = EventIngestNode(reasoner, storage)
        self.chain = IndustryChainNode(reasoner, storage)
        sources = []
        ts = TushareSource()
        sources.append(ts)
        from ..adapters.theme_index import ThemeIndex

        sources.append(ThemeIndex())
        self.cand = CandidateNode(reasoner, stock_master, storage, sources)

        # Phase 3 新增节点
        self.forward_mapping = ForwardMappingNode(reasoner, stock_master, storage)
        self.calibration = InitialCalibrationNode(quote_adapter, stock_master, storage, benchmark=self.benchmark)

    def forward(
        self,
        *,
        description: str,
        date_str: str,
        mode: str = "pre",  # "pre" | "post"
        top_k: int = 3,
        event_id: str = "forward",
    ) -> tuple[str, Path]:
        ctx = {"_replay_id": event_id}

        # 1. EventIngest (复用)
        event = self.ingest.run_cached({
            "description": description,
            "announcement_date": date_str,
            "reaction_date": date_str,
            "event_id": event_id,
            **ctx,
        })
        event = event.model_copy(update={"market": self.market})  # 多市场标记（Phase 5）

        # 2. FindSimilar (复用 Phase 2)
        results = self.ps.find_similar(
            event.description, top_k=top_k,
            shock_dimension=event.shock_dimension, impact_direction=event.impact_direction,
        )
        patterns = [p for p, _ in results]
        scores = [s for _, s in results]

        # 3. 判断降级层
        max_score = scores[0] if scores else 0.0
        if max_score > STRONG_THRESHOLD:
            level = "strong"
        elif max_score > WEAK_THRESHOLD:
            level = "weak"
        else:
            level = "qualitative"

        # 4. 路由
        if level in ("strong", "weak"):
            result = self._forward_with_patterns(event, patterns, scores, level, mode, event_id, ctx)
        else:
            result = self._forward_qualitative(event, mode, event_id, ctx)

        result.mode = mode
        result.degradation_level = level
        # qualitative 模式清空 matched（低分 Pattern 未用于预判，避免报告自相矛盾）
        if level == "qualitative":
            result.matched_pattern_ids = []
            result.matched_scores = []
            result.matched_patterns = []
        else:
            result.matched_pattern_ids = [p.source_replay_id for p in patterns]
            result.matched_scores = scores
            result.matched_patterns = patterns

        # 5. 报告
        markdown = self._generate_report(result)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        from ..measurement.report_template import report_filename
        path = self.reports_dir / f"{report_filename(description, date_str)}.md"
        path.write_text(markdown, encoding="utf-8")
        return markdown, path

    def _forward_with_patterns(
        self, event, patterns, scores, level, mode, event_id, ctx
    ) -> ForwardResult:
        """Pattern 指导路径（strong/weak）。"""
        mapping_out = self.forward_mapping.run_cached({
            "event": event,
            "patterns": patterns,
            "scores": scores,
            "degradation_level": level,
            **ctx,
        })

        beneficiaries: list[ForwardPrediction] = list(mapping_out.beneficiary_predictions)
        victims: list[ForwardPrediction] = list(mapping_out.victim_predictions)

        # 盘后校准（仅 strong + post）
        if mode == "post" and level == "strong":
            beneficiaries = self.calibration.run({
                "event": event, "predictions": beneficiaries, **ctx,
            })
            victims = self.calibration.run({
                "event": event, "predictions": victims, **ctx,
            })

        return ForwardResult(
            event_id=event_id,
            event=event,
            transmission_logic=mapping_out.transmission_logic,
            beneficiary_predictions=beneficiaries,
            victim_predictions=victims,
            confidence=mapping_out.confidence,
            key_risks=mapping_out.key_risks,
            mapping_errors=mapping_out.errors,
        )

    def _forward_qualitative(self, event, mode, event_id, ctx) -> ForwardResult:
        """纯定性路径（复用 Phase 1 IndustryChain + CandidateGen）。"""
        chain_nodes = self.chain.run_cached({"event": event, **ctx})
        candidates = self.cand.run_cached({
            "event": event, "chain_nodes": chain_nodes, **ctx,
        })

        # 转为 ForwardPrediction（无预期 CAR 区间）
        beneficiaries = [
            ForwardPrediction(
                stock_id=c["stock_id"],
                stock_name=c.get("name"),
                direction=c["direction"],
                rationale=c.get("rationale", ""),
            )
            for c in candidates
            if c["direction"] == StockDirection.BENEFICIARY
        ]
        victims = [
            ForwardPrediction(
                stock_id=c["stock_id"],
                stock_name=c.get("name"),
                direction=c["direction"],
                rationale=c.get("rationale", ""),
            )
            for c in candidates
            if c["direction"] == StockDirection.VICTIM
        ]

        return ForwardResult(
            event_id=event_id,
            event=event,
            transmission_logic="（纯定性模式：无相似历史 Pattern，基于事件逻辑推理）",
            beneficiary_predictions=beneficiaries,
            victim_predictions=victims,
            confidence="低",
            key_risks="无历史类比，预判基于事件逻辑推理，不确定性高",
            chain_nodes=chain_nodes,
        )

    def _generate_report(self, result: ForwardResult) -> str:
        """生成前瞻报告 markdown（确定性 Python 模板，非 LLM）。"""
        lines: list[str] = []
        title = result.event.description[:40]
        lines.append(f"# {title} 前瞻预判报告\n")

        level_label = {"strong": "强 Pattern 指导", "weak": "弱 Pattern 参考", "qualitative": "纯定性推理"}
        lines.append(f"> 模式：{'盘前前瞻' if result.mode == 'pre' else '盘后校准'} | {level_label.get(result.degradation_level, '?')} | 置信度：{result.confidence}\n")

        # forward_mapping 失败显著提示
        if result.mapping_errors:
            lines.append("> ⚠️ **forward_mapping 推理失败**：" + "；".join(result.mapping_errors))
            lines.append("> 预判结果可能不完整，失败方向的标的表为空。建议重跑或换 haiku 模型。\n")

        # 1 事件摘要
        lines.append("## 1 事件摘要")
        lines.append(f"- 描述：{result.event.description}")
        lines.append(f"- 日期：{result.event.reaction_date}")
        lines.append(f"- 类型：{result.event.event_type}")
        lines.append("")

        # 2 相似 Pattern（含事件影响定性，供本次类比参考）
        if result.matched_patterns:
            lines.append("## 2 相似历史 Pattern（含事件影响定性，供本次类比参考）\n")
            lines.append("| Pattern | 相似度 | 维度 | 方向 | 程度 | 范围 | 持续 | 显著 | 超预期 | 事件摘要 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
            for p, score in zip(result.matched_patterns, result.matched_scores):
                q = p.qualitative
                mag = q.magnitude if q else "—"
                sco = q.scope if q else "—"
                per = q.persistence if q else "—"
                sig = q.significance if q else "—"
                sur = q.surprise if q else "—"
                sd = p.shock_dimension.value if p.shock_dimension else "—"
                idir = p.impact_direction.value if p.impact_direction else "—"
                summary = (p.event_summary or "")[:30].replace("|", "/")
                lines.append(
                    f"| {p.source_replay_id[:20]} | {score:.2f} | {sd} | {idir} | "
                    f"{mag} | {sco} | {per} | {sig} | {sur} | {summary} |"
                )
            lines.append("")

        # 3 预判标的
        lines.append("## 3 预判受益/受损标的\n")

        header = "| 代码 | 名称 | 方向 | 预期CAR[+20] | 预期CAR[+60] | 逻辑 |"
        sep = "|---|---|---|---|---|---|"
        if result.mode == "post" and result.degradation_level == "strong":
            header = "| 代码 | 名称 | 方向 | 预期CAR[+20] | 预期CAR[+60] | 实际[+1] | 实际[+5] | 校准 | 逻辑 |"
            sep = "|---|---|---|---|---|---|---|---|---|"

        for label, preds in [("受益", result.beneficiary_predictions), ("受损", result.victim_predictions)]:
            if not preds:
                continue
            lines.append(f"### {label}（{len(preds)}）")
            lines.append(header)
            lines.append(sep)
            for p in preds:
                exp20 = p.expected_car_0_20_range or "—"
                exp60 = p.expected_car_0_60_range or "—"
                rat = p.rationale[:50].replace("|", "/")
                if result.mode == "post" and result.degradation_level == "strong":
                    a1 = f"{p.actual_car_0_1:+.1%}" if p.actual_car_0_1 is not None else "—"
                    a5 = f"{p.actual_car_0_5:+.1%}" if p.actual_car_0_5 is not None else "—"
                    cal = p.calibration or "—"
                    lines.append(
                        f"| {p.stock_id} | {p.stock_name or '—'} | {p.direction.value} | "
                        f"{exp20} | {exp60} | {a1} | {a5} | {cal} | {rat} |"
                    )
                else:
                    lines.append(
                        f"| {p.stock_id} | {p.stock_name or '—'} | {p.direction.value} | "
                        f"{exp20} | {exp60} | {rat} |"
                    )
            lines.append("")

        # 4 传导逻辑
        lines.append("## 4 综合传导逻辑")
        lines.append(result.transmission_logic or "（无）")
        lines.append("")

        # 5 风险
        if result.key_risks:
            lines.append("## 5 关键风险与预期差")
            lines.append(result.key_risks)
            lines.append("")

        # 6 局限
        lines.append("## 6 局限")
        lines.append("- **前瞻预判 ≠ 确定结论**：历史不一定重演；预期区间是参考非保证")
        lines.append(f"- 参考层级：{level_label.get(result.degradation_level, '?')}")
        if result.matched_pattern_ids:
            lines.append(f"- 基于历史 Pattern 样本：{len(result.matched_pattern_ids)} 个")
        else:
            lines.append("- 无历史 Pattern 类比，纯定性推理")
        if result.mode == "post":
            lines.append("- 盘后校准仅 [+1]/[+5] 初始信号，不等于最终验证")

        return "\n".join(lines)
