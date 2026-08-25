"""Event / EventEpisode：事件实体（EventIngest 产出，全流水线起点）。"""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from .common import AnnouncementTime, ImpactDirection, ShockDimension


class EventEpisode(BaseModel):
    """单个子事件（渐变事件 EventEpisode 的一个时点）。

    单事件场景下 Event.episodes 恰为 1 元素；每个 episode 拥有自己的
    reaction_date/announcement_date/公告时点（spec §4.6）。
    """

    id: str
    announcement_date: date
    reaction_date: date
    announcement_time: AnnouncementTime | None = None
    summary: str | None = None


class Event(BaseModel):
    """重大事件。度量以 reaction_date（首子事件或唯一子事件）为 t=0。"""

    id: str
    description: str
    announcement_date: date
    reaction_date: date
    announcement_time: AnnouncementTime | None = None
    event_type: str = Field(description="事件类型（开放词表：政策/监管/地缘/疫情/技术突破/宏观…）")
    shock_dimension: ShockDimension | None = None
    impact_direction: ImpactDirection | None = None
    key_entities: list[str] = Field(default_factory=list)
    episodes: list[EventEpisode] = Field(default_factory=list)
    source_url: str | None = None
    market: str = "A股"  # "A股" | "港股"，路由数据源（Phase 5）

    @model_validator(mode="after")
    def _episodes_nonempty(self) -> "Event":
        # 单事件：未提供 episodes 时，按 reaction_date 构造恰好 1 元素（spec §3/§4.6）
        if not self.episodes:
            self.episodes = [
                EventEpisode(
                    id=f"{self.id}-ep0",
                    announcement_date=self.announcement_date,
                    reaction_date=self.reaction_date,
                    announcement_time=self.announcement_time,
                    summary=self.description,
                )
            ]
        return self

    @property
    def is_episode(self) -> bool:
        return len(self.episodes) > 1
