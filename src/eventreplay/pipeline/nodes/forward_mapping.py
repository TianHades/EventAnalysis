"""ForwardMappingNode：LLM 综合多 Pattern → 前瞻预判（Phase 3）。

拆分为 2 次子调用（受益/受损），避开 sonnet 在网关下的 ~200s 超时：单次调用生成 >200s
被网关掐断，拆分后每次任务量减半、<200s 可完成；合并后产出与单次版一致。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...adapters.stock_master import StockMaster
from ...llm import Reasoner
from ...schema import ForwardPrediction, StockDirection
from ...schema.pattern import Pattern
from .base import LLMNode


class _PredictedStockOut(BaseModel):
    code: str
    name: str
    direction: str
    expected_car_0_20: str = ""
    expected_car_0_60: str = ""
    rationale: str


class _BenefOut(BaseModel):
    transmission_logic: str  # 受益侧传导逻辑
    beneficiaries: list[_PredictedStockOut] = Field(default_factory=list)
    confidence: str = "中"
    key_risks: str = ""


class _VictOut(BaseModel):
    transmission_logic: str  # 受损侧传导逻辑
    victims: list[_PredictedStockOut] = Field(default_factory=list)


# 仅用于 prompt_hash 指纹（拆分版两次子调用共用 common prompt + 此 schema 指纹）
class _ForwardMappingOut(BaseModel):
    transmission_logic: str
    beneficiary_predictions: list[_PredictedStockOut] = Field(default_factory=list)
    victim_predictions: list[_PredictedStockOut] = Field(default_factory=list)
    confidence: str = "中"
    key_risks: str = ""


class ForwardMappingResult(BaseModel):
    """run() 的合并产出（声明为 output_type，保证 SQLite 缓存命中后重建为模型对象）。"""

    transmission_logic: str = ""
    beneficiary_predictions: list[ForwardPrediction] = Field(default_factory=list)
    victim_predictions: list[ForwardPrediction] = Field(default_factory=list)
    confidence: str = "中"
    key_risks: str = ""
    errors: list[str] = Field(default_factory=list)


_COMMON_SYSTEM = (
    "你是 A 股前瞻映射分析师。基于新事件 + N 个相似历史 Pattern（每个含事件签名、传导模板、"
    "典型受益/受损标的及其历史 CAR），综合预判本次事件可能受益/受损的 A 股标的。\n\n"
    "要求：\n"
    "① 预期 CAR 区间：参考历史 Pattern 的 observed_car，结合事件特异性调整\n"
    "② 标的须为真实 A 股代码（系统会反查）\n"
    "③ 传导逻辑须有因果推导（事件→产业环节→标的）\n"
    "④ 弱参考模式给更宽区间、更低置信度\n"
    "⑤ key_risks 指出预期差风险（哪些逻辑可能被市场否认）\n"
    "⑥ 输出规模：每方向仅 2-3 只最具代表性标的；rationale ≤ 50 字；"
    "transmission_logic ≤ 200 字；key_risks ≤ 100 字。聚焦质量而非数量。\n"
    "只输出 JSON。"
)


class ForwardMappingNode(LLMNode):
    """LLM 读新事件 + top-K Pattern → 综合预判标的 + 预期区间 + 传导逻辑。

    拆分为受益/受损两次子调用，避开 sonnet 网关 ~200s 超时。
    """

    node_id = "forward_mapping"
    output_model = _ForwardMappingOut  # 用于 prompt_hash 指纹
    output_type = ForwardMappingResult  # SQLite 缓存重建（dict 载荷 → 模型对象）
    SUBCALL_MAX_ATTEMPTS = 2  # 子调用重试次数（含首次；sonnet 网关偶发超时重试）

    def __init__(self, reasoner: Reasoner, stock_master: StockMaster, storage=None) -> None:
        super().__init__(reasoner, storage)
        self.sm = stock_master

    def system_prompt(self) -> str:
        """common prompt（用于 prompt_hash 指纹）。"""
        return _COMMON_SYSTEM

    def _benef_system(self) -> str:
        return _COMMON_SYSTEM + "\n\n本次只预判【受益】标的 + 受益侧传导逻辑 + 整体置信度与关键风险。"

    def _vict_system(self) -> str:
        return _COMMON_SYSTEM + "\n\n本次只预判【受损】标的 + 受损侧传导逻辑。"

    def build_user(self, inputs: dict[str, Any]) -> str:
        import json

        event = inputs["event"]
        patterns: list[Pattern] = inputs.get("patterns", [])
        level = inputs.get("degradation_level", "strong")
        scores = inputs.get("scores", [])

        patterns_data = []
        for i, p in enumerate(patterns):
            patterns_data.append({
                "rank": i + 1,
                "similarity": f"{scores[i]:.2f}" if i < len(scores) else "?",
                "event_type": p.event_type,
                "shock_dimension": p.shock_dimension.value,
                "impact_direction": p.impact_direction.value,
                "industries": p.industries,
                "event_summary": p.event_summary,
                "transmission_template": p.transmission_template[:300],
                "beneficiaries": [
                    {"node": s.industry_node, "stocks": s.stocks,
                     "car_0_20": s.observed_car_0_20, "car_0_60": s.observed_car_0_60}
                    for s in p.archetypal_beneficiaries
                ],
                "victims": [
                    {"node": s.industry_node, "stocks": s.stocks,
                     "car_0_20": s.observed_car_0_20, "car_0_60": s.observed_car_0_60}
                    for s in p.archetypal_victims
                ],
                "qualitative": p.qualitative.model_dump() if p.qualitative else None,
            })

        market_hint = "港股标的（5位代码如00700）" if event.market == "港股" else "A股标的（6位代码）"
        return (
            f"市场：{market_hint}\n新事件：{event.description}\n"
            f"类型：{event.event_type} | 冲击维度：{event.shock_dimension.value if event.shock_dimension else '?'}\n"
            f"参考强度：{level}\n\n"
            f"相似历史 Pattern：\n{json.dumps(patterns_data, ensure_ascii=False, default=str)}"
        )

    def _build_preds(
        self, preds: list[_PredictedStockOut], pattern_ids: list[str]
    ) -> list[ForwardPrediction]:
        out: list[ForwardPrediction] = []
        for p in preds:
            resolved = self.sm.resolve(p.code) or self.sm.resolve(p.name)
            if resolved is None:
                continue
            try:
                d = StockDirection(p.direction)
            except ValueError:
                continue
            out.append(
                ForwardPrediction(
                    stock_id=resolved.code,
                    stock_name=resolved.name,
                    direction=d,
                    expected_car_0_20_range=p.expected_car_0_20 or None,
                    expected_car_0_60_range=p.expected_car_0_60 or None,
                    rationale=p.rationale,
                    source_pattern_ids=pattern_ids,
                )
            )
        return out

    def _call_with_retry(self, system: str, user: str, output_model: type) -> tuple[Any, str | None]:
        """子调用 + 重试（捕获 LLM 失败/子进程超时 TimeoutExpired）。返回 (结果, 错误信息)。"""
        last_err: str | None = None
        for _ in range(self.SUBCALL_MAX_ATTEMPTS):
            try:
                return self.reasoner.call(system=system, user=user, output_model=output_model), None
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:120]}"
        return None, last_err

    def run(self, inputs: dict[str, Any]) -> ForwardMappingResult:
        """拆分两次子调用（受益/受损）+ 重试，合并产出；失败方向置空并记 errors。"""
        user = self.build_user(inputs)
        benef, benef_err = self._call_with_retry(self._benef_system(), user, _BenefOut)
        vict, vict_err = self._call_with_retry(self._vict_system(), user, _VictOut)
        pattern_ids = [p.source_replay_id for p in inputs.get("patterns", [])]

        logic_parts: list[str] = []
        if benef and benef.transmission_logic:
            logic_parts.append(benef.transmission_logic)
        if vict and vict.transmission_logic:
            logic_parts.append(f"【受损侧】{vict.transmission_logic}")
        logic = "\n\n".join(logic_parts)

        errors: list[str] = []
        if benef_err:
            errors.append(f"受益方向预判失败: {benef_err}")
        if vict_err:
            errors.append(f"受损方向预判失败: {vict_err}")

        return ForwardMappingResult(
            transmission_logic=logic,
            beneficiary_predictions=self._build_preds(benef.beneficiaries, pattern_ids) if benef else [],
            victim_predictions=self._build_preds(vict.victims, pattern_ids) if vict else [],
            confidence=benef.confidence if benef else "低（推理失败）",
            key_risks=benef.key_risks if benef else "",
            errors=errors,
        )
