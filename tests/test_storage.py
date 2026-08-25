"""Storage 测试：实体往返、节点缓存命中/失效、断点续跑、skip_log、度量幂等迁移。"""

import sqlite3
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


def test_measurement_save_idempotent(tmp_path):
    """同 (replay_id, stock_id, episode_id) 重复写入 → 覆盖而非翻倍。

    场景：batch_replay 中 replay 成功、extract_pattern 失败 → 断点重跑 →
    旧实现纯 INSERT 导致度量翻倍、Pattern 统计失真。
    """
    with Storage(tmp_path / "t.db") as st:
        m = ImpactMeasurement(stock_id="600519", episode_id="e0", direction=StockDirection.BENEFICIARY)
        st.save_measurement(m, "r1")
        st.save_measurement(m.model_copy(update={"car_0_20": -0.05}), "r1")
        ms = st.get_measurements("r1")
        assert len(ms) == 1, "重复度量应覆盖，不得翻倍"
        assert ms[0].car_0_20 == -0.05, "覆盖后应取最新 payload"


def test_measurement_new_schema_has_unique(tmp_path):
    """新库 measurements 建表即带 (replay_id, stock_id, episode_id) 唯一约束。"""
    with Storage(tmp_path / "t.db") as st:
        cols = {
            tuple(r["name"] for r in st._conn.execute(f"PRAGMA index_info('{idx['name']}')"))
            for idx in st._conn.execute("PRAGMA index_list(measurements)")
            if idx["unique"]
        }
        assert ("replay_id", "stock_id", "episode_id") in cols


def test_measurement_migration_dedupes_legacy_rows(tmp_path):
    """存量库（旧 DDL 无唯一约束）已有重复度量 → 打开时去重（保留 MIN(id)）+ 建唯一索引。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_id TEXT NOT NULL,
            stock_id TEXT NOT NULL,
            episode_id TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """
    )
    def _payload(stock_id: str, car: float) -> str:
        return ImpactMeasurement(stock_id=stock_id, episode_id="e0", car_0_20=car).model_dump_json()

    conn.executemany(
        "INSERT INTO measurements(replay_id, stock_id, episode_id, payload) VALUES ('r1','600519','e0',?)",
        [(_payload("600519", -0.01),), (_payload("600519", -0.02),), (_payload("600519", -0.03),)],  # 3 行同键重复
    )
    conn.execute(
        "INSERT INTO measurements(replay_id, stock_id, episode_id, payload) VALUES ('r1','000001','e0',?)",
        (_payload("000001", -0.04),),
    )
    conn.commit()
    conn.close()

    with Storage(db) as st:
        ms = st.get_measurements("r1")
        assert len(ms) == 2, "重复组只保留 MIN(id) 一行"
        cars = {m.stock_id: m.car_0_20 for m in ms}
        assert cars["600519"] == -0.01, "保留最早一行"
        # 迁移后再写入同键 → 幂等
        st.save_measurement(ImpactMeasurement(stock_id="600519", episode_id="e0", car_0_20=-0.99), "r1")
        assert len(st.get_measurements("r1")) == 2
        assert {m.car_0_20 for m in st.get_measurements("r1")} == {-0.99, -0.04}


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


# ---- Fix 9b: list_patterns 单查询 + embedding 往返 ----


def _pattern_payload(pid: str, summary: str = "s") -> dict:
    return {
        "id": f"pat-{pid}", "source_replay_id": pid, "created_at": "2026-01-01T00:00:00",
        "model_alias": "m", "prompt_hash": "h", "shock_dimension": "政策",
        "impact_direction": "利好", "event_type": "t", "event_summary": summary,
        "transmission_template": "tt",
    }


def test_list_patterns_single_query_with_embedding(tmp_path):
    """list_patterns 返回全部 Pattern 且 embedding/model 完整（不再 N+1）。"""
    import json

    import numpy as np

    with Storage(tmp_path / "t.db") as st:
        emb = np.arange(8, dtype=np.float32)
        for pid in ("p1", "p2"):
            st._conn.execute(
                "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, created_at) "
                "VALUES (?,?,?,?,?)",
                (pid, json.dumps(_pattern_payload(pid), ensure_ascii=False), emb.tobytes(), "bge", "2026-01-01"),
            )
        st._conn.commit()
        ps = st.list_patterns()
        assert [p.source_replay_id for p in ps] == ["p1", "p2"]
        assert ps[0].embedding == emb.tolist()
        assert ps[0].embedding_model == "bge"


# ---- Fix 5b: search_patterns 降级统计告警 + 0/0 防护 ----


def test_search_patterns_warns_on_dim_mismatch_and_null(tmp_path, capsys):
    """维度不一致与 embedding NULL 的命中数统计并打印警告（不再静默降级）。"""
    import json

    import numpy as np

    with Storage(tmp_path / "t.db") as st:
        st._conn.execute(
            "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, created_at) VALUES ('m1',?,?,?,?)",
            (json.dumps(_pattern_payload("m1"), ensure_ascii=False),
             np.ones(512, dtype=np.float32).tobytes(), "bge", "2026-01-01"),
        )  # 维度不匹配（查询 2048）
        st._conn.execute(
            "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, created_at) VALUES ('m2',?,?,?,?)",
            (json.dumps(_pattern_payload("m2"), ensure_ascii=False), None, None, "2026-01-01"),
        )  # embedding NULL
        st._conn.execute(
            "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, created_at) VALUES ('ok',?,?,?,?)",
            (json.dumps(_pattern_payload("ok"), ensure_ascii=False),
             np.ones(2048, dtype=np.float32).tobytes(), "zhipu", "2026-01-01"),
        )
        st._conn.commit()
        results = st.search_patterns(list(np.ones(2048, dtype=np.float32)), top_k=5)
        assert len(results) == 2  # NULL embedding 行被结果集排除（沿用原语义），但必须被统计告警
        scores = {p.source_replay_id: s for p, s in results}
        assert scores["m1"] == 0.0
        assert scores["ok"] > 0.99
        out = capsys.readouterr().out
        assert "[storage]" in out and "1 条 Pattern 维度不匹配" in out and "1 条缺 embedding" in out, out


def test_search_patterns_zero_query_vector_no_nan(tmp_path):
    """全零查询向量：cosine 分母防 0/0，得 0 分而非 NaN。"""
    import json

    import numpy as np

    with Storage(tmp_path / "t.db") as st:
        st._conn.execute(
            "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, created_at) VALUES ('p1',?,?,?,?)",
            (json.dumps(_pattern_payload("p1"), ensure_ascii=False),
             np.ones(512, dtype=np.float32).tobytes(), "bge", "2026-01-01"),
        )
        st._conn.commit()
        results = st.search_patterns([0.0] * 512, top_k=3)
        assert len(results) == 1
        assert results[0][1] == 0.0  # 非 NaN（旧行为 0/0 → nan，污染排序）


# ---- Fix 9c: list_recent_seen 门面 ----


def test_list_recent_seen_by_purpose(tmp_path):
    with Storage(tmp_path / "t.db") as st:
        st.mark_seen("k1", "news", '{"description": "a"}', "push")
        st.mark_seen("k2", "news", '{"description": "b"}', "identified")
        st.mark_seen("k1", "news", '{"description": "a"}', "push")  # 幂等
        rows = st.list_recent_seen("push", limit=10)
        assert len(rows) == 1 and rows[0]["dedup_key"] == "k1"
        assert st.list_recent_seen("identified")[0]["dedup_key"] == "k2"
        assert st.list_recent_seen("replay") == []
