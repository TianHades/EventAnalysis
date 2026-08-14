"""EventExtractor：新闻/候选 → 结构化 Event + 4级打分（Phase 4 共享 LLM 节点）。

输入 NewsItem → LLM 判断 is_event + 结构化 Event + significance(1-4)/a_share_relevance(1-5)
→ post_process 生成 CandidateEvent（含 dedup_key）；is_event=False 返回 None（噪音过滤）。
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ...llm import Reasoner
from ...schema import CandidateEvent, Event, ImpactDirection, ShockDimension
from ...schema.news import NewsItem
from ..event_dedup import make_dedup_key
from .base import LLMNode


class _EventExtractOut(BaseModel):
    is_event: bool
    event_type: str
    shock_dimension: str | None = None
    impact_direction: str | None = None
    description: str
    announcement_date: str  # YYYY-MM-DD
    reaction_date: str | None = None
    key_entities: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    significance: int  # 1-4
    a_share_relevance: int  # 1-5
    surprise_timing: int = 3  # 1-5：1=完全预期内，5=完全突发
    surprise_magnitude: int = 3  # 1-5：1=符合预期，5=远超预期
    impact_scale: int = 3  # 1-5：预估影响幅度（5=极大>20%，1=微<5%）
    rationale: str = ""


def _clamp(v: int, lo: int, hi: int) -> int:
    """LLM 打分越界兜底（significance 1-4 / a_share 1-5）。"""
    return max(lo, min(hi, v))


class EventExtractor(LLMNode):
    """LLM 读 NewsItem → 判断是否事件 + 结构化 Event + 4级打分 → CandidateEvent | None。"""

    node_id = "event_extract"
    output_model = _EventExtractOut

    def __init__(self, reasoner: Reasoner, storage=None) -> None:
        super().__init__(reasoner, storage)

    def system_prompt(self) -> str:
        return (
            "你是 A 股事件分析师。判断给定信息是否构成值得复盘/前瞻的重大事件。\n\n"
            "要求：\n"
            "① is_event：是否构成重大事件（过滤个股财报/日常波动/无产业链传导的噪音）\n"
            "② 重大性打分（1-4）：4=系统级（冲击全市场，如联储议息/关税战/疫情/重大地缘）；"
            "3=主线级（引领市场主线、多板块传导，如芯片管制/AI算力/创新药出海）；"
            "2=行业级（单一产业链冲击）；1=主题/噪音（短期概念炒作或个股无传导）\n"
            "③ A股可达性（1-5）：是否有明确可映射的 A 股标的（5=直接受益/受损明确；1=无关）\n"
            "④ 突发性（1-5）：1=完全预期内（预告/渐进/日程已知/延续既有主线），5=完全突发（无预警/无铺垫/全新方向）。\n"
            "   **关键**：如果「近期事件上下文」显示同类事件已持续发生（如中东冲突已持续数周），"
            "突发性应降至 1-2 分（市场已定价/已预期）。\n"
            "⑤ 超预期性（1-5）：1=符合市场共识预期，5=远超预期（力度/范围/时点超预期）。\n"
            "   **关键**：延续既有主线的事件（如伊朗局势延续），超预期性应降至 1-2 分；"
            "只有力度/范围发生质变才给高分。\n"
            "⑥ 影响规模（1-5）：预估相关标的CAR幅度，5=极大(>20%)，4=大(10-20%)，3=中(5-10%)，2=小(<5%)，1=微(几乎无反应)。\n"
            "   **注意**：已充分预期/已定价的事件，即使本身重要，对股价的边际影响也应下调（3→2→1）。\n"
            "⑦ 事件结构化：event_type / shock_dimension(供给/需求/政策/流动性/情绪) / "
            "impact_direction(利好/利空/中性/混合) / description / announcement_date(YYYY-MM-DD) / "
            "reaction_date / key_entities / industries\n"
            "只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        news: NewsItem = inputs["news"]
        parts = [
            f"新闻标题：{news.title}",
            f"来源：{news.source}",
            f"时间：{news.published_at}",
            f"正文：{news.content}",
        ]
        # 注入近期事件上下文（校准突发性/超预期性打分）
        recent = inputs.get("recent_context", "")
        if recent:
            parts.append(f"\n---\n近期事件上下文（用于校准突发性/超预期性，判断本条新闻是全新事件还是延续既有主线）：\n{recent}")
        return "\n".join(parts)

    def post_process(self, llm_output: _EventExtractOut, inputs: dict[str, Any]) -> CandidateEvent | None:
        """is_event=False → None（过滤噪音）；否则 → CandidateEvent（含 dedup_key）。"""
        if not llm_output.is_event:
            return None
        news: NewsItem = inputs["news"]
        event = self._build_event(llm_output)
        dedup_key = make_dedup_key(
            event.event_type, str(event.announcement_date), event.key_entities,
            description=event.description,
        )
        return CandidateEvent(
            event=event,
            significance=_clamp(llm_output.significance, 1, 4),
            a_share_relevance=_clamp(llm_output.a_share_relevance, 1, 5),
            surprise_timing=_clamp(llm_output.surprise_timing, 1, 5),
            surprise_magnitude=_clamp(llm_output.surprise_magnitude, 1, 5),
            impact_scale=_clamp(llm_output.impact_scale, 1, 5),
            source="news",
            dedup_key=dedup_key,
            news_id=news.id,
            industries=llm_output.industries,
        )

    @staticmethod
    def _build_event(out: _EventExtractOut) -> Event:
        def _parse(d: str | None) -> date | None:
            if not d:
                return None
            try:
                return date.fromisoformat(str(d)[:10])
            except ValueError:
                return None

        ann = _parse(out.announcement_date)
        react = _parse(out.reaction_date) or ann
        if ann is None:
            # LLM 返回非法日期时兜底（与 historical_miner 一致），避免 Event 校验崩溃
            ann = react = date.today()
        try:
            sd = ShockDimension(out.shock_dimension) if out.shock_dimension else None
        except ValueError:
            sd = None
        try:
            idir = ImpactDirection(out.impact_direction) if out.impact_direction else None
        except ValueError:
            idir = None
        # 稳定 id：description 的 md5（避免 Python hash 随机化）
        eid = f"evt-{hashlib.md5(out.description.encode('utf-8')).hexdigest()[:12]}"
        return Event(
            id=eid,
            description=out.description,
            announcement_date=ann,
            reaction_date=react,
            event_type=out.event_type,
            shock_dimension=sd,
            impact_direction=idir,
            key_entities=out.key_entities,
        )
