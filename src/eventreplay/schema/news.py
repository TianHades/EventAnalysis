"""Phase 4 事件采集数据模型：NewsItem / CandidateEvent / MiningJob。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from .event import Event


class NewsItem(BaseModel):
    """新闻原文（NewsAdapter 产出）。"""

    id: str
    title: str
    content: str = ""
    published_at: datetime | None = None
    source: str = ""  # "stock_news_main_cx" | "news_cctv" | "jiuyan" | ...
    url: str | None = None


class CandidateEvent(BaseModel):
    """抽取的事件 + 打分（EventExtractor 产出，功能1/2 共享）。"""

    event: Event
    significance: int  # 1-4（4系统级/3主线级/2行业级/1主题噪音）
    a_share_relevance: int  # 1-5 A股标的可达性
    surprise_timing: int = 3  # 1-5：1=完全预期内，5=完全突发（无预警）
    surprise_magnitude: int = 3  # 1-5：1=符合预期，5=远超预期
    impact_scale: int = 3  # 1-5：预估影响幅度（5=极大CAR>20%，3=中5-10%，1=微<5%）
    source: str = ""  # "news" | "llm_knowledge" | "calendar"
    dedup_key: str = ""  # 事件类型+日期+关键实体 hash（EventDedup 用）
    news_id: str | None = None  # 来源新闻（功能1）
    status: str = "pending"  # pending/replayed/pushed/skipped
    industries: list[str] = Field(default_factory=list)  # 事件行业标签（审核/多样性用）


class MiningJob(BaseModel):
    """功能2建库任务（HistoricalEventMiner + BatchReplayRunner 状态）。"""

    id: str
    start_date: date
    end_date: date
    filters: dict = Field(default_factory=dict)  # {industry, shock_dimension, top_n}
    candidates: list[CandidateEvent] = Field(default_factory=list)
    progress: dict = Field(default_factory=dict)  # {total, replayed, failed, skipped}
    created_at: datetime | None = None
