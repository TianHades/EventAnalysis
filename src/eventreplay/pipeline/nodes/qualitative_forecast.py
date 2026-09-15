"""QualitativeForecastNode：事件五维定性预判（三层分工：程序锚点 / LLM 判定 / 事后对账）。

设计动机：五维定性（程度/范围/持续/显著/超预期）本质是模糊判断，纯程序打分会丢文本信息
（如"保险首次纳入注资"只存在于公告文本），纯 LLM 会漂移——分工为：
- 证据层（程序）：collect_anchors 算事件前 20 日基准/当事标的收益（surprise 的"已定价程度"
  代理）、相似历史 Pattern 实测五维众数（先例统计基）。全容错，缺锚点不阻塞。
- 判定层（LLM）：带锚点综合五维 + 逐维置信度；锚点冲突须在 rationale 写取舍依据。
- 校准层（程序）：calibrate_qualitative（nodes/calibration.py）事后与实测 PatternQualitative
  逐维对账；"低置信度判对/高置信度判错"的维度对账信息量最大。

LLM 失败 → 先例众数兜底（source="fallback"，置信度全标低），报告打 banner。
"""

from __future__ import annotations

from datetime import timedelta
from statistics import mode
from typing import Any

from pydantic import BaseModel, Field

from ...llm import LLMNodeFailed, Reasoner
from ...schema import ForwardPrediction
from ...schema.forward import QualitativeForecast
from ...schema.pattern import Pattern, QUALITATIVE_LABELS
from .base import LLMNode

_DIM_KEYS = tuple(QUALITATIVE_LABELS)  # 五维键（单一事实源：schema/pattern.py）
ANCHOR_TARGETS = 3  # 个股 pre_20 锚点取受益池前 N 只（控制行情拉取次数）


def _pre20_return(qa, fetch, reaction_date) -> float | None:
    """事件前 20 交易日收益（严格早于 reaction_date 的最后 20 根 K 线）。"""
    df = fetch(reaction_date - timedelta(days=60), reaction_date)
    rows = [(r["date"], float(r["close"])) for _, r in df.iterrows() if r["date"] < reaction_date]
    rows.sort(key=lambda x: x[0])
    if len(rows) < 2:
        return None
    window = rows[-20:]
    return window[-1][1] / window[0][1] - 1.0


def collect_anchors(
    event,
    patterns: list[Pattern],
    beneficiaries: list[ForwardPrediction],
    qa,
    benchmark: str,
    anchor_targets: int = ANCHOR_TARGETS,
) -> dict[str, Any]:
    """证据层：程序可测的客观锚点（surprise 判定的"已定价程度"代理 + 先例统计基）。

    全容错：任一锚点失败仅缺失该项，不抛异常（DeadQA 场景）。
    """
    anchors: dict[str, Any] = {}

    # 1) 基准事件前 20 交易日收益（事件前大盘是否已抢跑）
    bench_ret: float | None = None
    try:
        bench_ret = _pre20_return(qa, lambda s, e: qa.index_daily(benchmark, s, e), event.reaction_date)
        if bench_ret is not None:
            anchors["bench_pre_20"] = f"{bench_ret:+.1%}"
    except Exception:
        pass

    # 2) 当事（受益）标的事件前 20 交易日收益与超额（相对基准，同为小数口径）
    targets: dict[str, str] = {}
    for pred in beneficiaries[:anchor_targets]:
        try:
            ret = _pre20_return(qa, lambda s, e, c=pred.stock_id: qa.daily_adjusted(c, s, e), event.reaction_date)
            if ret is None:
                continue
            if bench_ret is not None:
                targets[pred.stock_id] = f"{ret:+.1%}(超额{ret - bench_ret:+.1%})"
            else:
                targets[pred.stock_id] = f"{ret:+.1%}"
        except Exception:
            continue
    if targets:
        anchors["targets_pre_20"] = targets

    # 3) 先例五维众数（相似历史 Pattern 的实测定性统计基）
    majority: dict[str, str] = {}
    for dim in _DIM_KEYS:
        vals = [getattr(p.qualitative, dim) for p in patterns if p.qualitative and getattr(p.qualitative, dim)]
        if vals:
            majority[dim] = mode(vals)
    if majority:
        anchors["pattern_majority"] = majority

    return anchors


class _ForecastOut(BaseModel):
    """LLM 输出契约（档位非法值由 QualitativeForecast validator 归 None）。"""

    magnitude: str | None = None
    scope: str | None = None
    persistence: str | None = None
    significance: str | None = None
    surprise: str | None = None
    dim_confidence: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""


_SYSTEM = (
    "你是 A 股事件前瞻定性分析师。对新事件做五维定性【预判】（事前判断，非事后复盘）：\n"
    "程度 magnitude（极大/大/中/小）、范围 scope（广/中/窄）、"
    "持续性 persistence（长期/中期/短期脉冲）、显著性 significance（显著/不显著）、"
    "超预期性 surprise（超预期/符合预期/利好出尽）。\n\n"
    "程序已注入客观锚点（user 内容里 anchors 字段：事件前 20 日基准与当事标的涨跌、"
    "相似历史 Pattern 实测五维众数等）。要求：\n"
    "① 逐维综合锚点与事件文本判断；锚点冲突时必须在 rationale 写明取舍依据\n"
    "② 每维给 dim_confidence（高/中/低）：拿不准就标低，不得全标高\n"
    "③ surprise 重点判『事前已定价程度』：锚点显示事件前已抢跑→利好出尽风险；"
    "无预期铺垫且影响大→超预期；有预期铺垫但部分未定价→符合预期\n"
    "④ 锚点缺失的维度基于事件文本推理，不得整维留空\n"
    "只输出 JSON。"
)


class QualitativeForecastNode(LLMNode):
    """五维定性预判节点（证据层注入 + LLM 判定 + 先例众数兜底）。"""

    node_id = "qualitative_forecast"
    output_model = _ForecastOut
    output_type = QualitativeForecast  # SQLite 缓存重建（dict 载荷 → 模型对象）

    def __init__(self, reasoner: Reasoner, quote_adapter, storage=None, benchmark: str = "sh000300") -> None:
        super().__init__(reasoner, storage)
        self.qa = quote_adapter
        self.benchmark = benchmark

    def _collect(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """锚点单次计算：run() 先算存 self._anchors，build_user/post_process/_fallback 共享
        （避免同一次推理内重复拉行情，且保证 prompt 与 anchors_used 严格一致）。"""
        try:
            return collect_anchors(
                inputs["event"], inputs.get("patterns", []),
                inputs.get("beneficiaries", []), self.qa, self.benchmark,
            )
        except Exception:
            return {}  # collect_anchors 自身全容错，此处双保险

    def _shared_anchors(self, inputs: dict[str, Any]) -> dict[str, Any]:
        anchors = getattr(self, "_anchors", None)
        return anchors if anchors is not None else self._collect(inputs)

    def system_prompt(self) -> str:
        return _SYSTEM

    def build_user(self, inputs: dict[str, Any]) -> str:
        import json

        event = inputs["event"]
        patterns: list[Pattern] = inputs.get("patterns", [])
        beneficiaries: list[ForwardPrediction] = inputs.get("beneficiaries", [])

        anchors = self._shared_anchors(inputs)

        hist = [
            {
                "event_summary": p.event_summary,
                "qualitative": p.qualitative.model_dump() if p.qualitative else None,
            }
            for p in patterns
        ]
        return json.dumps({
            "event": {
                "description": event.description,
                "announcement_date": str(event.announcement_date),
                "reaction_date": str(event.reaction_date),
                "event_type": event.event_type,
            },
            "similar_patterns": hist,
            "beneficiary_targets": [
                {"code": b.stock_id, "name": b.stock_name} for b in beneficiaries
            ],
            "anchors": anchors,
        }, ensure_ascii=False)

    def post_process(self, llm_output: BaseModel, inputs: dict[str, Any]) -> QualitativeForecast:
        anchors = self._shared_anchors(inputs)
        flat: list[str] = []
        if "bench_pre_20" in anchors:
            flat.append(f"bench_pre_20={anchors['bench_pre_20']}")
        flat += [f"{c}_pre_20={v}" for c, v in anchors.get("targets_pre_20", {}).items()]
        flat += [f"pattern_majority.{k}={v}" for k, v in anchors.get("pattern_majority", {}).items()]

        out: _ForecastOut = llm_output  # type: ignore[assignment]
        return QualitativeForecast(
            magnitude=out.magnitude, scope=out.scope, persistence=out.persistence,
            significance=out.significance, surprise=out.surprise,
            # dim_confidence 非法键/值由 schema validator 兜底过滤（单一权威）
            dim_confidence=out.dim_confidence, rationale=out.rationale,
            anchors_used=flat, source="llm",
        )

    def run(self, inputs: dict[str, Any]) -> QualitativeForecast:
        self._anchors = self._collect(inputs)  # 单次计算，本次推理全程共享
        try:
            return super().run(inputs)
        except LLMNodeFailed:
            return self._fallback(inputs)

    def _fallback(self, inputs: dict[str, Any]) -> QualitativeForecast:
        """LLM 失败兜底：相似先例五维众数（无先例 → 全 None，诚实缺省）。"""
        majority: dict[str, str] = dict(self._shared_anchors(inputs).get("pattern_majority", {}))
        return QualitativeForecast(
            **majority,
            dim_confidence={dim: "低" for dim in majority},
            rationale="LLM 推理失败，程序以相似先例五维众数兜底（无先例维度无法预判）",
            anchors_used=[f"pattern_majority.{k}={v}" for k, v in majority.items()],
            source="fallback",
        )
