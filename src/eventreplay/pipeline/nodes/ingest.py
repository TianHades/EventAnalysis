"""EventIngest：事件原文 → 结构化 Event（含 reaction_date/公告时点，spec §4.1）。"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ...schema import Event, ImpactDirection, ShockDimension
from .base import LLMNode


class _IngestOut(BaseModel):
    event_type: str
    shock_dimension: str | None = None
    impact_direction: str | None = None
    key_entities: list[str] = Field(default_factory=list)


class EventIngestNode(LLMNode):
    node_id = "event_ingest"
    output_model = _IngestOut
    output_type = Event  # SQLite 缓存重建

    def system_prompt(self) -> str:
        return (
            "你是 A 股事件研究分析师。把给定事件描述结构化：判定事件类型"
            "（如 政策/监管/地缘/疫情/技术突破/宏观/公司）、冲击维度（供给/需求/政策/流动性/情绪）、"
            "影响方向（利好/利空/中性/混合）、关键实体。只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        return f"事件描述：{inputs['description']}\n公告日：{inputs.get('announcement_date')}"

    def post_process(self, llm_output: _IngestOut, inputs: dict[str, Any]) -> Event:
        def _parse(d: str) -> date:
            return date.fromisoformat(str(d)[:10])

        ann = _parse(inputs["announcement_date"])
        react = _parse(inputs.get("reaction_date") or inputs["announcement_date"])
        try:
            sd = ShockDimension(llm_output.shock_dimension) if llm_output.shock_dimension else None
        except ValueError:
            sd = None
        try:
            idir = ImpactDirection(llm_output.impact_direction) if llm_output.impact_direction else None
        except ValueError:
            idir = None
        return Event(
            id=str(inputs.get("event_id", "evt")),
            description=inputs["description"],
            announcement_date=ann,
            reaction_date=react,
            event_type=llm_output.event_type,
            shock_dimension=sd,
            impact_direction=idir,
            key_entities=llm_output.key_entities,
        )
