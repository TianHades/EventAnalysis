"""PatternService 集成测试：extract_pattern + find_similar（mock Reasoner/Embedder）。"""

from datetime import date

from eventreplay.llm import Embedder
from eventreplay.pipeline.nodes.pattern import _PatternLLM, _ArchetypalStockOut
from eventreplay.pipeline.pattern_service import PatternService
from eventreplay.schema import (
    AttributionResult,
    Event,
    ImpactMeasurement,
    PatternQualitative,
    PoolMember,
    StockDirection,
)
from eventreplay.storage import Storage


class _FakeReasoner:
    model_alias = "fake"

    def call(self, *, system, user, output_model):
        assert output_model is _PatternLLM
        return _PatternLLM(
            shock_dimension="政策",
            impact_direction="利空",
            event_type="监管",
            industries=["教育", "传媒"],
            event_summary="双减政策打压K12校外学科类培训，传导至教培/广告/出版。",
            transmission_template="政策 → K12教培业务萎缩 → 广告/出版需求下降；职业教育承接转型。",
            archetypal_beneficiaries=[_ArchetypalStockOut(industry_node="职业教育", stocks=["002607"], observed_car_0_20=-0.09)],
            archetypal_victims=[_ArchetypalStockOut(industry_node="K12教培", stocks=["000526"], observed_car_0_20=-0.09)],
        )


class _FakeEmbedder(Embedder):
    def __init__(self):
        super().__init__()

    def embed(self, text):
        return [0.1] * Embedder.EMBEDDING_DIM


def _seed_replay(storage: Storage, replay_id: str) -> None:
    e = Event(
        id=replay_id,
        description="双减政策落地",
        announcement_date=date(2021, 7, 24),
        reaction_date=date(2021, 7, 26),
        event_type="监管",
    )
    storage.save_event(e)
    ar = AttributionResult(
        beneficiary_pool=[PoolMember(stock_id="002607", direction=StockDirection.BENEFICIARY, episode_id=f"{replay_id}-ep0")],
        victim_pool=[PoolMember(stock_id="000526", direction=StockDirection.VICTIM, episode_id=f"{replay_id}-ep0")],
    )
    storage.save_attribution(ar, replay_id)
    storage.save_measurement(
        ImpactMeasurement(stock_id="000526", episode_id=f"{replay_id}-ep0", car_0_20=-0.09), replay_id
    )


def test_extract_pattern_produces_and_saves(tmp_path):
    storage = Storage(tmp_path / "t.db")
    _seed_replay(storage, "r1")
    svc = PatternService(storage, _FakeReasoner(), _FakeEmbedder())
    p = svc.extract_pattern("r1")
    assert p is not None
    assert p.source_replay_id == "r1"
    assert p.shock_dimension.value == "政策"
    assert p.impact_direction.value == "利空"
    assert len(p.embedding) == Embedder.EMBEDDING_DIM
    assert len(p.archetypal_victims) == 1
    # 已入库且可取回
    got = storage.get_pattern("r1")
    assert got is not None and got.event_summary.startswith("双减")


def test_find_similar_retrieves_pattern(tmp_path):
    storage = Storage(tmp_path / "t.db")
    _seed_replay(storage, "r1")
    svc = PatternService(storage, _FakeReasoner(), _FakeEmbedder())
    svc.extract_pattern("r1")
    res = svc.find_similar("新的教培监管事件", top_k=3)
    assert len(res) == 1
    assert res[0][0].source_replay_id == "r1"
    assert res[0][1] > 0.99  # 与自身 embedding 完全一致 → cosine≈1


def test_extract_unknown_replay_returns_none(tmp_path):
    storage = Storage(tmp_path / "t.db")
    svc = PatternService(storage, _FakeReasoner(), _FakeEmbedder())
    assert svc.extract_pattern("nope") is None


class _FakeReasonerQualitative(_FakeReasoner):
    """返回含事件级定性的 Pattern。"""

    def call(self, *, system, user, output_model):
        base = super().call(system=system, user=user, output_model=output_model)
        return base.model_copy(update={"qualitative": PatternQualitative(
            magnitude="大", scope="中", persistence="中期", significance="显著", surprise="超预期",
            rationale="池|caar|>10%且多标的",
            pool_caar_beneficiary=-0.09, pool_caar_victim=-0.12,
        )})


def test_extract_pattern_with_qualitative(tmp_path):
    """extract_pattern LLM 提炼定性 → Pattern.qualitative 落库且可取回。"""
    storage = Storage(tmp_path / "t.db")
    _seed_replay(storage, "r1")
    svc = PatternService(storage, _FakeReasonerQualitative(), _FakeEmbedder())
    p = svc.extract_pattern("r1")
    assert p is not None and p.qualitative is not None
    assert p.qualitative.magnitude == "大"
    assert p.qualitative.significance == "显著"
    assert p.qualitative.surprise == "超预期"
    got = storage.get_pattern("r1")
    assert got is not None and got.qualitative is not None
    assert got.qualitative.scope == "中"


def test_old_pattern_payload_without_qualitative_loads(tmp_path):
    """旧 Pattern payload（无 qualitative 字段）反序列化不报错，qualitative=None。"""
    import json

    storage = Storage(tmp_path / "t.db")
    old_payload = json.dumps({
        "id": "pat-r2", "source_replay_id": "r2",
        "created_at": "2026-01-01T00:00:00",
        "model_alias": "fake", "prompt_hash": "x", "embedding_model": None,
        "shock_dimension": "政策", "impact_direction": "利空",
        "event_type": "监管", "industries": [], "event_summary": "旧",
        "embedding": None, "transmission_template": "t",
        "archetypal_beneficiaries": [], "archetypal_victims": [],
        "evidence_replay_ids": ["r2"],
    })
    storage._conn.execute(
        "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, "
        "shock_dimension, impact_direction, industries, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("r2", old_payload, None, None, "政策", "利空", "", "2026-01-01T00:00:00"),
    )
    storage._conn.commit()
    p = storage.get_pattern("r2")
    assert p is not None and p.qualitative is None
