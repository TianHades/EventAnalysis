"""ReplayLoader：从 Storage 组装完整 Replay 上下文（Phase 2 PatternExtractor 输入）。"""

from __future__ import annotations

from typing import Any

from ..storage import Storage


class ReplayLoader:
    """按 replay_id（= Phase 1 event_id）从 4 张表加载并组装 Replay 上下文。"""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def load(self, replay_id: str) -> dict[str, Any] | None:
        event = self.storage.get_event(replay_id)
        if event is None:
            return None
        return {
            "event": event,
            "chain": self.storage.get_chain(replay_id),
            "attribution_result": self.storage.get_attribution(replay_id),
            "measurements": self.storage.get_measurements(replay_id),
        }
