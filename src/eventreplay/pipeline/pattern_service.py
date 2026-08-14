"""PatternService：规律提炼 + 相似检索（Phase 2 对外门面）。"""

from __future__ import annotations

from ..llm import Embedder, Reasoner
from ..schema import ImpactDirection, Pattern, ShockDimension
from ..storage import Storage
from .nodes.pattern import PatternExtractor
from .replay_loader import ReplayLoader


class PatternService:
    """extract_pattern(replay_id) + find_similar(desc)。"""

    def __init__(self, storage: Storage, reasoner: Reasoner, embedder: Embedder) -> None:
        self.storage = storage
        self.embedder = embedder
        self.loader = ReplayLoader(storage)
        self.extractor = PatternExtractor(reasoner, embedder, storage)

    def extract_pattern(self, replay_id: str) -> Pattern | None:
        ctx = self.loader.load(replay_id)
        if ctx is None:
            return None
        pattern = self.extractor.run_cached(
            {"replay_context": ctx, "source_replay_id": replay_id, "_replay_id": replay_id}
        )
        self.storage.save_pattern(pattern)
        return pattern

    def find_similar(
        self,
        desc: str,
        *,
        shock_dimension: ShockDimension | str | None = None,
        impact_direction: ImpactDirection | str | None = None,
        industry: str | None = None,
        top_k: int = 3,
    ) -> list[tuple[Pattern, float]]:
        q = self.embedder.embed(desc)
        return self.storage.search_patterns(
            q,
            shock_dimension=shock_dimension,
            impact_direction=impact_direction,
            industry=industry,
            top_k=top_k,
        )
