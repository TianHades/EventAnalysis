"""EventDedup：事件去重（seen_events 表，跨运行持久）。"""

from __future__ import annotations

import hashlib

from ..schema import CandidateEvent
from ..storage import Storage


def make_dedup_key(
    event_type: str, date_str: str, key_entities: list[str], description: str = ""
) -> str:
    """事件类型 + 日期 + 全量标准化描述 → md5 hash。

    用 description（稳定）替代 entities（多变），同事件不同报道/entities 也能去重。
    规范化全文参与 hash、不截断：截断（旧实现 [:100]）会让前 100 字相同、
    结论相反的长描述合并成同 key。
    description 为空时回退到 sorted(key_entities)（向后兼容）。
    """
    if description:
        # 标准化：去空白/标点/转小写（全文参与 hash，不截断）
        import re
        norm = re.sub(r"[\s　\W]+", "", description.lower())
        raw = f"{event_type}|{date_str}|{norm}"
    else:
        raw = f"{event_type}|{date_str}|{','.join(sorted(key_entities))}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class EventDedup:
    """事件去重：基于 dedup_key 的 seen_events 表（跨运行持久）。"""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def is_seen(self, dedup_key: str, purpose: str | None = None) -> bool:
        return self.storage.is_seen(dedup_key, purpose)

    def filter_new(self, candidates: list[CandidateEvent], purpose: str | None = None) -> list[CandidateEvent]:
        """返回未见过的候选（dedup_key 未在 seen_events）；空 key 跳过。"""
        return [
            c for c in candidates
            if c.dedup_key and not self.storage.is_seen(c.dedup_key, purpose)
        ]

    def mark_seen(self, candidates: list[CandidateEvent], purpose: str = "") -> None:
        """标记候选为已见（幂等：INSERT OR IGNORE，purpose 区分 replay/push）。"""
        for c in candidates:
            if c.dedup_key:
                self.storage.mark_seen(c.dedup_key, c.source, c.event.model_dump_json(), purpose)
