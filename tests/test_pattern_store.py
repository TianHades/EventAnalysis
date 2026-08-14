"""PatternStore 测试：save/get 往返、幂等、cosine 检索、枚举/子串过滤、null embedding。"""

from datetime import datetime

from eventreplay.schema import ImpactDirection, Pattern, ShockDimension
from eventreplay.storage import Storage


def _pattern(
    rid: str,
    emb: list[float] | None,
    *,
    sd: ShockDimension = ShockDimension.POLICY,
    idir: ImpactDirection = ImpactDirection.NEGATIVE,
    industries: list[str] | None = None,
) -> Pattern:
    return Pattern(
        id=f"p-{rid}",
        source_replay_id=rid,
        created_at=datetime(2026, 7, 1),
        model_alias="sonnet",
        prompt_hash="h",
        embedding_model="embedding-3" if emb else None,
        shock_dimension=sd,
        impact_direction=idir,
        event_type="监管",
        industries=industries if industries is not None else ["教育", "传媒"],
        event_summary=f"事件摘要 {rid}",
        embedding=emb,
        transmission_template="事件→产业→标的",
        evidence_replay_ids=[rid],
    )


def test_pattern_roundtrip_preserves_embedding(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        emb = [0.1, 0.2, 0.3, 0.4]
        st.save_pattern(_pattern("r1", emb))
        got = st.get_pattern("r1")
        assert got is not None
        assert got.embedding is not None
        assert len(got.embedding) == 4
        assert abs(got.embedding[0] - 0.1) < 1e-6
        assert got.shock_dimension == ShockDimension.POLICY


def test_pattern_idempotent_overwrites(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.save_pattern(_pattern("r1", [1.0, 0.0, 0.0, 0.0], industries=["旧"]))
        st.save_pattern(_pattern("r1", [0.0, 1.0, 0.0, 0.0], industries=["新"]))  # 同 source_replay_id
        ps = st.list_patterns()
        assert len(ps) == 1, "同 source_replay_id 应覆盖，不新增"
        assert ps[0].industries == ["新"]


def test_pattern_null_embedding_excluded_from_vector_search(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.save_pattern(_pattern("r1", None))  # 无 embedding
        res = st.search_patterns([1.0, 0.0, 0.0, 0.0])
        assert res == [], "null embedding 不进向量检索"
        # 仅结构化检索仍可命中
        res2 = st.search_patterns(None, shock_dimension=ShockDimension.POLICY)
        assert len(res2) == 1


def test_cosine_search_ranks_nearest_first(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.save_pattern(_pattern("a", [1.0, 0.0, 0.0, 0.0]))
        st.save_pattern(_pattern("b", [0.0, 1.0, 0.0, 0.0]))
        st.save_pattern(_pattern("c", [0.9, 0.1, 0.0, 0.0]))
        res = st.search_patterns([1.0, 0.0, 0.0, 0.0], top_k=3)
        ids = [p.source_replay_id for p, _ in res]
        assert ids[0] == "a", "最相似应排第一（cosine=1）"
        assert ids[1] == "c", "c 次之（与 query 接近）"
        assert ids[2] == "b"
        # 分数单调递减
        scores = [s for _, s in res]
        assert scores[0] > scores[1] > scores[2]


def test_search_enum_and_substring_filter(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.save_pattern(_pattern("a", [1, 0, 0, 0], sd=ShockDimension.POLICY, industries=["教育"]))
        st.save_pattern(
            _pattern("b", [1, 0, 0, 0], sd=ShockDimension.SUPPLY, industries=["电子"])
        )
        # 枚举过滤
        res = st.search_patterns([1, 0, 0, 0], shock_dimension=ShockDimension.POLICY)
        assert [p.source_replay_id for p, _ in res] == ["a"]
        # 子串过滤
        res2 = st.search_patterns([1, 0, 0, 0], industry="电子")
        assert [p.source_replay_id for p, _ in res2] == ["b"]
