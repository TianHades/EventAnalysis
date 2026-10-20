"""ForwardOrchestrator：前瞻映射路由（三层降级）+ 串联节点。"""

from __future__ import annotations

from pathlib import Path

from ..adapters.quote_adapter import AkshareQuoteAdapter
from ..adapters.stock_master import StockMaster
from ..llm import Reasoner
from ..measurement.report_template import DISCLAIMER
from ..pipeline.nodes.calibration import InitialCalibrationNode
from ..pipeline.nodes.forward_mapping import ForwardMappingNode
from ..pipeline.nodes.qualitative_forecast import ANCHOR_TARGETS, QualitativeForecastNode
from ..pipeline.nodes.ingest import EventIngestNode
from ..pipeline.nodes.chain import IndustryChainNode
from ..pipeline.nodes.candidate import CandidateNode
from ..pipeline.orchestrator import make_recall_sources
from ..pipeline.pattern_service import PatternService
from ..schema import ForwardPrediction, ForwardResult, StockDirection
from ..storage import Storage

# 降级阈值（cosine similarity）：实测同类 0.74-0.81，无关 0.35-0.40
STRONG_THRESHOLD = 0.6
WEAK_THRESHOLD = 0.45  # 0.3 偏低（无关事件 0.35-0.40 误进 weak）→ 0.45

# 校准状态中文标签（schema/forward.py 的 6 值 Literal；裸英文直出报告不可读）
_CALIBRATION_LABELS = {
    "confirming": "吻合",
    "diverging": "背离",
    "neutral": "中性",
    "pending": "数据窗不足",
    "no_range": "无预期区间",
    "parse_error": "区间不可解析",
}


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
        self._ts, sources = make_recall_sources()
        self.cand = CandidateNode(reasoner, stock_master, storage, sources)

        # Phase 3 新增节点
        self.forward_mapping = ForwardMappingNode(reasoner, stock_master, storage)
        self.calibration = InitialCalibrationNode(quote_adapter, stock_master, storage, benchmark=self.benchmark)
        # 五维定性预判（事件级；证据层锚点 + LLM 判定 + 先例众数兜底）
        self.qualitative_forecast = QualitativeForecastNode(
            reasoner, quote_adapter, storage, benchmark=self.benchmark
        )

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

        # 2. FindSimilar (复用 Phase 2)；degraded=embed 失败 → 检索不可用（纯结构化）
        # 不传维度/方向过滤（spec §4.3）：LLM 对同一事件的 impact_direction 解析
        # （如"利好"）与 Pattern 标注（如"混合"）口径不一致时会整批误杀强向量匹配
        # （实测 2019 沙特油田被袭 cosine 0.777 被 direction 严格相等过滤剔掉 → 误落定性层）
        results, retrieval_degraded = self.ps.find_similar(
            event.description, top_k=top_k,
        )
        patterns = [p for p, _ in results]
        scores = [s for _, s in results]

        # 3. 判断降级层（检索不可用时分数全为 0，自然落入 qualitative，但语义不同）
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

        # 4.5 事件五维定性预判（qualitative 层传空 patterns，与报告 §2 口径一致）。
        # 节点内已有两层兜底（collect_anchors 全容错 + LLM 失败→先例众数），外层 try 仅防
        # 缓存读写/模型构造等意外——失败不阻塞主报告，但异常摘要写入报告（不静默吞）。
        try:
            result.qualitative_forecast = self.qualitative_forecast.run_cached({
                "event": event,
                "patterns": patterns if level in ("strong", "weak") else [],
                "beneficiaries": result.beneficiary_predictions[:ANCHOR_TARGETS],
                **ctx,
            })
        except Exception as exc:
            result.qualitative_forecast = None
            result.qualitative_forecast_error = f"{type(exc).__name__}: {exc}"[:200]

        # 5. 报告（retrieval_degraded 用于区分"库空/真无匹配/检索不可用"三态）
        markdown = self._generate_report(result, retrieval_degraded=retrieval_degraded)
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

    def _pattern_lib_empty(self) -> bool:
        """规律库是否为空（区分"库空"与"有库无匹配"；无法判定时保守返回 False）。"""
        st = getattr(self.ps, "storage", None) or self.storage
        if st is None:
            return False
        try:
            return len(st.list_patterns()) == 0
        except Exception:
            return False

    def _generate_report(self, result: ForwardResult, retrieval_degraded: bool = False) -> str:
        """生成前瞻报告 markdown（确定性 Python 模板，非 LLM）。

        retrieval_degraded=True（embed 失败）时明示"向量检索不可用"，
        不得谎称"无历史 Pattern 类比"。
        """
        lines: list[str] = []
        title = (result.event.description or "—")[:40]
        lines.append(f"# {title} 前瞻预判报告\n")

        level_label = {"strong": "强 Pattern 指导", "weak": "弱 Pattern 参考", "qualitative": "纯定性推理"}
        # mode 二分：仅 post 显示"盘后校准"、pre 显示"盘前前瞻"，其它原样展示
        mode_label = {"pre": "盘前前瞻", "post": "盘后校准"}.get(result.mode, result.mode)
        lines.append(f"> 模式：{mode_label} | {level_label.get(result.degradation_level, '?')} | 置信度：{result.confidence or '—'}\n")

        # 检索不可用 banner（必须在定性结论之前，避免被误读为已比对历史）
        if retrieval_degraded:
            lines.append("> ⚠️ **向量检索不可用，结论为纯定性推理**（本次未与历史 Pattern 库比对，不代表\"无历史类比\"）\n")

        # forward_mapping 失败显著提示
        if result.mapping_errors:
            lines.append("> ⚠️ **forward_mapping 推理失败**：" + "；".join(result.mapping_errors))
            lines.append("> 预判结果可能不完整，失败方向的标的表为空。建议重跑或换 haiku 模型。\n")

        # 1 事件摘要（字段 Optional 化后 None → '—'）
        lines.append("## 1 事件摘要")
        lines.append(f"- 描述：{result.event.description or '—'}")
        lines.append(f"- 日期：{result.event.reaction_date or '—'}")
        lines.append(f"- 类型：{result.event.event_type or '—'}")
        lines.append("")

        # 1.5 事件定性预判（五维，事前；编号 1.5 保持 spec §6 章节号稳定）
        lines.append("## 1.5 事件定性预判（五维，事前）")
        qf = result.qualitative_forecast
        if qf is None:
            reason = result.qualitative_forecast_error or "原因未知"
            lines.append(f"- （定性预判节点未产出：{reason}）")
        else:
            lines.append("| 维度 | 预判 | 置信度 |")
            lines.append("|---|---|---|")
            for key, label in (
                ("magnitude", "程度"), ("scope", "范围"), ("persistence", "持续"),
                ("significance", "显著"), ("surprise", "超预期"),
            ):
                val = getattr(qf, key)
                lines.append(f"| {label} {key} | {val if val else '—'} | {qf.dim_confidence.get(key, '—')} |")
            lines.append("")
            if qf.rationale:
                lines.append(f"- 依据：{qf.rationale}")
            if qf.anchors_used:
                lines.append(f"- 程序锚点：{'；'.join(qf.anchors_used[:6])}")
            if qf.source == "fallback":
                lines.append("- ⚠️ LLM 推理失败，以上为程序兜底（先例众数），置信度低")
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
                rat = (p.rationale or "—")[:50].replace("|", "/")
                if result.mode == "post" and result.degradation_level == "strong":
                    a1 = f"{p.actual_car_0_1:+.1%}" if p.actual_car_0_1 is not None else "—"
                    a5 = f"{p.actual_car_0_5:+.1%}" if p.actual_car_0_5 is not None else "—"
                    cal = _CALIBRATION_LABELS.get(p.calibration, p.calibration) if p.calibration else "—"
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

        # 6 局限（三态：库空 / 真无匹配 / 检索不可用）
        lines.append("## 6 局限")
        lines.append("- **前瞻预判 ≠ 确定结论**：历史不一定重演；预期区间是参考非保证")
        lines.append(f"- 参考层级：{level_label.get(result.degradation_level, '?')}")
        if result.matched_pattern_ids:
            lines.append(f"- 基于历史 Pattern 样本：{len(result.matched_pattern_ids)} 个")
        elif retrieval_degraded:
            lines.append("- ⚠️ 向量检索不可用（embedding 失败）：本轮未与历史 Pattern 比对，不得据此声称\"无历史类比\"")
        elif self._pattern_lib_empty():
            lines.append("- 历史规律库为空：尚无可用 Pattern（先 replay + extract-pattern 建库）")
        else:
            lines.append("- 无相似历史 Pattern（相似度低于阈值），纯定性推理")
        if result.mode == "post":
            lines.append("- 盘后校准仅 [+1]/[+5] 初始信号，不等于最终验证")

        # 合规披露：前瞻报告是 CLI forward 与 monitor 自动 forward 的共同产物 → 末尾统一追加
        lines.append("")
        lines.append(DISCLAIMER)

        return "\n".join(lines)
