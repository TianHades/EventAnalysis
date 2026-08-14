"""Storage 测试：实体往返、节点缓存命中/失效、断点续跑、skip_log。"""

from datetime import date, datetime

from eventreplay.schema import (
    ChainNode,
    Event,
    ImpactMeasurement,
    Replay,
    StockDirection,
    TransmissionChain,
)
from eventreplay.storage import Storage


def make_event() -> Event:
    return Event(
        id="evt-1",
        description="双减政策落地",
        announcement_date=date(2021, 7, 24),
        reaction_date=date(2021, 7, 26),
        event_type="监管",
    )


def test_event_roundtrip(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        e = make_event()
        st.save_event(e)
        got = st.get_event("evt-1")
        assert got is not None
        assert got.reaction_date == date(2021, 7, 26)
        assert st.get_event("nope") is None


def test_replay_roundtrip_and_embedding_compat(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        r = Replay(
            id="r1",
            event_id="evt-1",
            pipeline_version="v1",
            prompt_hash="h",
            model_alias="sonnet",
            cli_version="2.1.177",
            started_at=datetime(2026, 6, 30, 10, 0),
            node_outputs={"ingest": {"x": 1}},
        )
        st.save_replay(r)
        got = st.get_replay("r1")
        assert got is not None and got.model_alias == "sonnet"


def test_measurements_append_and_list(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        m1 = ImpactMeasurement(stock_id="600519", episode_id="e0", direction=StockDirection.BENEFICIARY)
        m2 = ImpactMeasurement(stock_id="000001", episode_id="e0")
        st.save_measurement(m1, "r1")
        st.save_measurement(m2, "r1")
        ms = st.get_measurements("r1")
        assert len(ms) == 2
        assert ms[0].stock_id == "600519"


def test_chain_roundtrip(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        chain = TransmissionChain(event_id="evt-1", nodes=[ChainNode(id="n1", label="光模块", level="上游")])
        st.save_chain(chain)
        got = st.get_chain("evt-1")
        assert got is not None and got.nodes[0].label == "光模块"


def test_node_cache_hit_and_miss(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        assert st.get_cached_node("key-1") is None  # miss
        st.save_node_run(replay_id="r1", node_id="ingest", cache_key="key-1", status="done", payload={"a": 1})
        assert st.get_cached_node("key-1") == {"a": 1}  # hit


def test_cache_key_change_invalidates(tmp_path):
    """改 cache_key → 命中新 key、不命中旧 key。"""
    with Storage(tmp_path / "t.db") as st:
        st.save_node_run(replay_id="r1", node_id="ingest", cache_key="k-old", status="done", payload={"v": 1})
        st.save_node_run(replay_id="r1", node_id="ingest", cache_key="k-new", status="done", payload={"v": 2})
        # UNIQUE(replay_id,node_id) → 同节点新 cache_key 覆盖
        assert st.get_cached_node("k-old") is None
        assert st.get_cached_node("k-new") == {"v": 2}


def test_completed_nodes_for_resume(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.save_node_run(replay_id="r1", node_id="ingest", cache_key="k1", status="done")
        st.save_node_run(replay_id="r1", node_id="chain", cache_key="k2", status="done")
        st.save_node_run(replay_id="r1", node_id="cand", cache_key="k3", status="running")
        done = st.completed_nodes("r1")
        assert done == {"ingest", "chain"}


def test_skip_log(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.append_skip(replay_id="r1", stock_id="600001", episode_id="e0", reason="日线取不到")
        st.append_skip(replay_id="r1", reason="估计窗不足60日", stock_id="600002")
        skips = st.get_skips("r1")
        assert len(skips) == 2
        assert skips[0]["stock_id"] == "600001"
        assert "估计窗" in skips[1]["reason"]


def test_attribution_roundtrip(tmp_path):
    """Phase 2 前置：AttributionResult 持久化往返。"""
    from eventreplay.schema import AttributionResult, PoolMember, StockDirection

    with Storage(tmp_path / "t.db") as st:
        ar = AttributionResult(
            beneficiary_pool=[
                PoolMember(stock_id="a", direction=StockDirection.BENEFICIARY, episode_id="e0")
            ],
            victim_pool=[],
        )
        st.save_attribution(ar, "r1")
        got = st.get_attribution("r1")
        assert got is not None
        assert len(got.beneficiary_pool) == 1
        assert got.beneficiary_pool[0].stock_id == "a"
        assert st.get_attribution("nope") is None
