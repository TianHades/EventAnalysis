"""schema 构造/校验/序列化测试。"""

from datetime import date, datetime

import pytest

from eventreplay.schema import (
    AnnouncementTime,
    AttributionResult,
    Board,
    ChainNode,
    Event,
    EventEpisode,
    ImpactMeasurement,
    PoolMember,
    PoolStat,
    Replay,
    Stock,
    StockDirection,
    Tier,
    TransmissionChain,
)


def test_event_single_episode_degeneration():
    """单事件：未提供 episodes 时自动构造恰好 1 元素（spec §3/§4.6）。"""
    e = Event(
        id="evt-1",
        description="双减政策落地",
        announcement_date=date(2021, 7, 24),
        reaction_date=date(2021, 7, 26),
        announcement_time=AnnouncementTime.NON_TRADING,
        event_type="监管",
    )
    assert len(e.episodes) == 1
    assert e.episodes[0].reaction_date == date(2021, 7, 26)
    assert e.is_episode is False


def test_event_multi_episode():
    eps = [
        EventEpisode(id="e0", announcement_date=date(2024, 4, 12), reaction_date=date(2024, 4, 15)),
        EventEpisode(id="e1", announcement_date=date(2024, 4, 25), reaction_date=date(2024, 4, 26)),
    ]
    e = Event(
        id="evt-2",
        description="新国九条",
        announcement_date=date(2024, 4, 12),
        reaction_date=date(2024, 4, 15),
        event_type="政策",
        episodes=eps,
    )
    assert e.is_episode is True
    assert len(e.episodes) == 2


def test_event_episodes_cannot_be_empty_after_validation():
    """episodes 经 validator 保证非空（单事件自动填充）。"""
    e = Event(
        id="evt-3",
        description="x",
        announcement_date=date(2020, 2, 3),
        reaction_date=date(2020, 2, 3),
        event_type="疫情",
    )
    assert len(e.episodes) >= 1


def test_stock_defaults():
    s = Stock(code="600519", name="贵州茅台", board=Board.MAIN)
    assert s.historical_names == []
    assert s.is_st is False


def test_impact_measurement_defaults_and_conflict_flag():
    m = ImpactMeasurement(stock_id="600519", episode_id="e0", direction=StockDirection.BENEFICIARY)
    assert m.limit_up_days == 0
    assert m.direction_conflict is False
    assert m.low_confidence is False
    assert m.tier is None


def test_attribution_result_pool_of_and_low_n_nondisclosed():
    stat = PoolStat(n=3, caar=0.05, disclosed=False)  # N<5 → 不披露
    ar = AttributionResult(
        beneficiary_pool=[PoolMember(stock_id="a", direction=StockDirection.BENEFICIARY, episode_id="e0")],
        victim_pool=[],
        pool_stats={"beneficiary": stat},
    )
    assert len(ar.pool_of(StockDirection.BENEFICIARY)) == 1
    assert ar.pool_of(StockDirection.VICTIM) == []
    assert ar.pool_stats["beneficiary"].disclosed is False


def test_transmission_chain_low_confidence_ratio():
    chain = TransmissionChain(
        event_id="evt-1",
        nodes=[
            ChainNode(id="n1", label="光模块", level="上游"),
            ChainNode(id="n2", label="AI 服务器", level="中游", low_confidence=True),
        ],
    )
    assert chain.low_confidence_ratio == pytest.approx(0.5)


def test_replay_roundtrip_and_embedding_compat_fields():
    r = Replay(
        id="r1",
        event_id="evt-1",
        pipeline_version="v1",
        prompt_hash="abc",
        model_alias="sonnet",
        cli_version="2.1.177",
        started_at=datetime(2026, 6, 30, 10, 0),
        node_outputs={"ingest": {"ok": True}},
    )
    dumped = r.model_dump()
    restored = Replay.model_validate(dumped)
    assert restored.model_alias == "sonnet"
    assert restored.embedding_text is None  # Phase 2 前留空
    # JSON 序列化往返
    assert Replay.model_validate_json(r.model_dump_json()).id == "r1"


def test_enum_values_chinese():
    """枚举值为中文，便于报告直接使用。"""
    assert AnnouncementTime.AFTER_CLOSE.value == "盘后"
    assert StockDirection.VICTIM.value == "受损"
    assert Tier.STRONG.value == "强"


def test_board_hk_main():
    """Board 含港股主板（Phase 5）。"""
    assert Board.HK_MAIN.value == "港股主板"


def test_stock_hk_industry():
    """Stock 港股恒生行业字段 + market 港股。"""
    s = Stock(code="00700", name="腾讯控股", market="港股", board=Board.HK_MAIN, hk_industry="咨询服务")
    assert s.market == "港股"
    assert s.board == Board.HK_MAIN
    assert s.hk_industry == "咨询服务"


def test_event_market_default_and_hk():
    """Event market 默认 A股，可设港股（路由数据源）。"""
    e_a = Event(
        id="e1", description="测试", announcement_date=date(2024, 1, 1),
        reaction_date=date(2024, 1, 1), event_type="政策",
    )
    assert e_a.market == "A股"
    e_hk = Event(
        id="e2", description="港股事件", announcement_date=date(2024, 1, 1),
        reaction_date=date(2024, 1, 1), event_type="政策", market="港股",
    )
    assert e_hk.market == "港股"
