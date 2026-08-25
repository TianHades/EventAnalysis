"""EventIngest：事件原文 → 结构化 Event（含 reaction_date/公告时点，spec §4.1）。"""

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...schema import AnnouncementTime, Event, ImpactDirection, ShockDimension
from .base import LLMNode


class _IngestOut(BaseModel):
    event_type: str
    shock_dimension: str | None = None
    impact_direction: str | None = None
    announcement_time: Literal["盘前", "盘中", "盘后", "非交易日"] | None = None
    key_entities: list[str] = Field(default_factory=list)


class EventIngestNode(LLMNode):
    node_id = "event_ingest"
    output_model = _IngestOut
    output_type = Event  # SQLite 缓存重建

    def system_prompt(self) -> str:
        return (
            "你是 A 股事件研究分析师。把给定事件描述结构化：判定事件类型"
            "（如 政策/监管/地缘/疫情/技术突破/宏观/公司）、冲击维度（供给/需求/政策/流动性/情绪）、"
            "影响方向（利好/利空/中性/混合）、公告时点 announcement_time（盘前/盘中/盘后/非交易日；"
            "盘后=当日15:00后发布，非交易日=周末或节假日发布）、关键实体。只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        return f"事件描述：{inputs['description']}\n公告日：{inputs.get('announcement_date')}"

    def post_process(self, llm_output: _IngestOut, inputs: dict[str, Any]) -> Event:
        def _parse(d: str) -> date:
            try:
                return date.fromisoformat(str(d)[:10])
            except ValueError as e:
                raise ValueError(f"公告日期不可解析: {d!r}（需 YYYY-MM-DD）") from e

        ann = _parse(inputs["announcement_date"])
        if inputs.get("reaction_date"):
            react = _parse(inputs["reaction_date"])  # 显式传入的 reaction_date 优先
        elif llm_output.announcement_time in ("盘后", "非交易日"):
            # 盘后/非交易日公告 → 首个反应日为公告日次日（event_study t0 对齐再推到下一交易日）
            react = ann + timedelta(days=1)
        else:
            react = ann
        try:
            at = AnnouncementTime(llm_output.announcement_time) if llm_output.announcement_time else None
        except ValueError:
            at = None
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
            announcement_time=at,
            event_type=llm_output.event_type,
            shock_dimension=sd,
            impact_direction=idir,
            key_entities=llm_output.key_entities,
        )
