"""PatternExtractor 数值对账测试：LLM 抄录的 CAR / 池 caar 不得直接入库（防幻觉值
成为前瞻"预期 CAR"来源）；非法枚举 → None；embedding 失败告警。"""

from datetime import date

from eventreplay.pipeline.nodes.pattern import (
    PatternExtractor,
    _ArchetypalStockOut,
    _PatternLLM,
)
from eventreplay.schema import (
    AttributionResult,
    Event,
    ImpactMeasurement,
    PatternQualitative,
    PoolMember,
    PoolStat,
    StockDirection,
)


class FakeReasoner:
    model_alias = "fake"

    def __init__(self, out: _PatternLLM):
        self._out = out

    def call(self, *, system, user, output_model):
        assert output_model is _PatternLLM
        return self._out


_SENTINEL = object()


class FakeEmbedder:
    model_name = "fake-emb"

    def __init__(self, vec=_SENTINEL):
        self._vec = [0.1, 0.2] if vec is _SENTINEL else vec

    def embed(self, text):
        return self._vec


def _llm_out(**update) -> _PatternLLM:
    """LLM 输出：victims=000526（有度量）但 car 抄错；beneficiaries=002607（无度量）纯编造。"""
    base = _PatternLLM(
        shock_dimension="政策",
        impact_direction="利空",
        event_type="监管",
        event_summary="双减政策打压K12",
        transmission_template="政策→教培",
        archetypal_beneficiaries=[
            _ArchetypalStockOut(industry_node="职业教育", stocks=["002607"], observed_car_0_20=-0.99, observed_car_0_60=-0.88)
        ],
        archetypal_victims=[
            _ArchetypalStockOut(industry_node="K12教培", stocks=["000526"], observed_car_0_20=-0.55, observed_car_0_60=-0.66)
        ],
    )
    return base.model_copy(update=update) if update else base


def _replay_ctx(
    *,
    with_measurement: bool = True,
    pool_stats: dict | None = None,
) -> dict:
    e = Event(
        id="r1", description="双减", announcement_date=date(2021, 7, 24),
        reaction_date=date(2021, 7, 26), event_type="监管",
    )
    ms = (
        [ImpactMeasurement(stock_id="000526", episode_id="r1-ep0", car_0_20=-0.09, car_0_60=-0.15)]
        if with_measurement
        else []
    )
    ar = AttributionResult(
        beneficiary_pool=[PoolMember(stock_id="002607", direction=StockDirection.BENEFICIARY, episode_id="r1-ep0")],
        victim_pool=[PoolMember(stock_id="000526", direction=StockDirection.VICTIM, episode_id="r1-ep0")],
        pool_stats=pool_stats or {},
    )
    return {"event": e, "chain": None, "attribution_result": ar, "measurements": ms}


def _run(llm_out: _PatternLLM, ctx: dict, embedder=None) -> object:
    node = PatternExtractor(FakeReasoner(llm_out), embedder or FakeEmbedder())
    return node.run({"replay_context": ctx, "source_replay_id": "r1"})


def test_observed_car_backfilled_from_measurement():
    """有度量的典型标的 → observed_car 用度量真值回填（覆盖 LLM 抄录值）。"""
    p = _run(_llm_out(), _replay_ctx())
    v = p.archetypal_victims[0]  # 000526 有度量
    assert v.observed_car_0_20 == -0.09  # LLM 给 -0.55 被覆盖
    assert v.observed_car_0_60 == -0.15  # LLM 给 -0.66 被覆盖


def test_observed_car_matches_by_name_when_llm_uses_names():
    """LLM 违背 prompt 用股票名称（实测重提炼试点出现）→ 名称兜底索引仍能对账。"""
    ctx = _replay_ctx()
    ctx["measurements"] = [
        ImpactMeasurement(stock_id="000526", episode_id="r1-ep0", stock_name="学大教育", car_0_20=-0.09, car_0_60=-0.15)
    ]
    llm = _llm_out()
    llm = llm.model_copy(update={
        "archetypal_victims": [
            _ArchetypalStockOut(industry_node="K12教培", stocks=["学大教育"], observed_car_0_20=-0.55, observed_car_0_60=-0.66)
        ]
    })
    p = _run(llm, ctx)
    assert p.archetypal_victims[0].observed_car_0_20 == -0.09  # 名称命中，回填真值
    assert p.archetypal_victims[0].observed_car_0_60 == -0.15


def test_observed_car_none_when_stock_not_measured():
    """无度量的典型标的（LLM 编造 car）→ 两字段置 None（绝不采信 LLM 数字）。"""
    p = _run(_llm_out(), _replay_ctx())
    b = p.archetypal_beneficiaries[0]  # 002607 无度量，LLM 编了 -0.99/-0.88
    assert b.observed_car_0_20 is None
    assert b.observed_car_0_60 is None


def test_observed_car_partial_window():
    """度量只有 car_0_20（无 car_0_60）→ 0_20 回填、0_60 保持 None。"""
    ctx = _replay_ctx()
    ctx["measurements"] = [ImpactMeasurement(stock_id="000526", episode_id="r1-ep0", car_0_20=-0.09)]
    p = _run(_llm_out(), ctx)
    v = p.archetypal_victims[0]
    assert v.observed_car_0_20 == -0.09
    assert v.observed_car_0_60 is None


def test_invalid_shock_dimension_becomes_none():
    """非法 shock_dimension → None（不得静默改 POLICY——检索硬过滤键错桶=永远检索不到）。"""
    p = _run(_llm_out(shock_dimension="无效维度"), _replay_ctx())
    assert p.shock_dimension is None


def test_invalid_impact_direction_becomes_none():
    """非法 impact_direction → None。"""
    p = _run(_llm_out(impact_direction="不对"), _replay_ctx())
    assert p.impact_direction is None


def test_pool_caar_backfilled_from_pool_stats():
    """qualitative.pool_caar_* 从 replay_context 的 pool_stats 回填（覆盖 LLM 抄录）。"""
    stats = {
        "beneficiary": PoolStat(n=3, caar=0.12),
        "victim": PoolStat(n=5, caar=-0.08, grank_p=0.03, disclosed=True),
    }
    llm = _llm_out(qualitative=PatternQualitative(
        rationale="x", pool_caar_beneficiary=0.99, pool_caar_victim=-0.99,
    ))
    p = _run(llm, _replay_ctx(pool_stats=stats))
    assert p.qualitative is not None
    assert p.qualitative.pool_caar_beneficiary == 0.12  # LLM 给 0.99 被覆盖
    assert p.qualitative.pool_caar_victim == -0.08


def test_pool_caar_none_without_pool_stats():
    """无 pool_stats（或 caar 缺失）→ qualitative.pool_caar_* = None（不采信 LLM 值）。"""
    llm = _llm_out(qualitative=PatternQualitative(pool_caar_beneficiary=0.99, pool_caar_victim=-0.99))
    p = _run(llm, _replay_ctx(pool_stats={}))
    assert p.qualitative is not None
    assert p.qualitative.pool_caar_beneficiary is None
    assert p.qualitative.pool_caar_victim is None


def test_embedding_failure_warns_and_keeps_none(capsys):
    """embedding 失败 → 打印补救提示警告，embedding=None（对向量检索不可见）。"""
    p = _run(_llm_out(), _replay_ctx(), embedder=FakeEmbedder(vec=None))
    assert p.embedding is None
    assert p.embedding_model is None
    out = capsys.readouterr().out
    assert "embedding 失败" in out
    assert "pat-r1" in out
    assert "reembed_patterns" in out
