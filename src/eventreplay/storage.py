"""SQLite 存储层：实体 CRUD + 节点缓存/断点续跑 + skip_log。

设计：
- 实体（Event/Replay/ImpactMeasurement/TransmissionChain）以 JSON payload 整体存取，
  保证 schema 演进时存储层无需改表结构。
- pipeline_runs 同时承担"节点缓存（按 cache_key 命中）"与"断点续跑（按 run_id+node_id 列已完成）"。
- events/replays 预留 embedding_text/embedding_model 列（Phase 2 向量检索向前兼容）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .schema import (
    AttributionResult,
    Event,
    ImpactMeasurement,
    Pattern,
    Replay,
    TransmissionChain,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    embedding_text TEXT,
    embedding_model TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replays (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    embedding_text TEXT,
    embedding_model TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    replay_id TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chains (
    event_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    replay_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(replay_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_cache ON pipeline_runs(cache_key);
CREATE TABLE IF NOT EXISTS skip_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    replay_id TEXT NOT NULL,
    stock_id TEXT,
    episode_id TEXT,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attribution_results (
    replay_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS patterns (
    source_replay_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    embedding BLOB,
    embedding_model TEXT,
    shock_dimension TEXT,
    impact_direction TEXT,
    industries TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patterns_filter ON patterns(shock_dimension, impact_direction);
CREATE TABLE IF NOT EXISTS seen_events (
    dedup_key TEXT NOT NULL,
    source TEXT,
    payload TEXT,
    seen_at TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (dedup_key, purpose)
);
"""


class Storage:
    """SQLite 存储门面。单连接、check_same_thread=False（MVP 单线程编排）。"""

    def __init__(self, db_path: str | Path = "data/eventreplay.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        # migration: seen_events 加 purpose 列 + 复合 PK（旧表重建）
        try:
            _cols = [r[1] for r in self._conn.execute("PRAGMA table_info(seen_events)").fetchall()]
            if _cols and "purpose" not in _cols:
                self._conn.execute("DROP TABLE seen_events")
                self._conn.executescript(_SCHEMA)
        except Exception:
            pass
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- 实体 CRUD ----

    def save_event(self, event: Event) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO events(id, payload, embedding_text, embedding_model, created_at) "
            "VALUES (?, ?, NULL, NULL, ?)",
            (event.id, event.model_dump_json(), datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def get_event(self, event_id: str) -> Event | None:
        row = self._conn.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
        return Event.model_validate_json(row["payload"]) if row else None

    def save_replay(self, replay: Replay) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO replays(id, event_id, payload, embedding_text, embedding_model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                replay.id,
                replay.event_id,
                replay.model_dump_json(),
                replay.embedding_text,
                replay.embedding_model,
                datetime.utcnow().isoformat(),
            ),
        )
        self._conn.commit()

    def get_replay(self, replay_id: str) -> Replay | None:
        row = self._conn.execute("SELECT payload FROM replays WHERE id=?", (replay_id,)).fetchone()
        return Replay.model_validate_json(row["payload"]) if row else None

    def save_measurement(self, m: ImpactMeasurement, replay_id: str) -> None:
        self._conn.execute(
            "INSERT INTO measurements(replay_id, stock_id, episode_id, payload) VALUES (?, ?, ?, ?)",
            (replay_id, m.stock_id, m.episode_id, m.model_dump_json()),
        )
        self._conn.commit()

    def get_measurements(self, replay_id: str) -> list[ImpactMeasurement]:
        rows = self._conn.execute(
            "SELECT payload FROM measurements WHERE replay_id=? ORDER BY id", (replay_id,)
        ).fetchall()
        return [ImpactMeasurement.model_validate_json(r["payload"]) for r in rows]

    def save_chain(self, chain: TransmissionChain) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO chains(event_id, payload) VALUES (?, ?)",
            (chain.event_id, chain.model_dump_json()),
        )
        self._conn.commit()

    def get_chain(self, event_id: str) -> TransmissionChain | None:
        row = self._conn.execute("SELECT payload FROM chains WHERE event_id=?", (event_id,)).fetchone()
        return TransmissionChain.model_validate_json(row["payload"]) if row else None

    # ---- AttributionResult（Phase 2 前置补丁）----

    def save_attribution(self, ar: AttributionResult, replay_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO attribution_results(replay_id, payload) VALUES (?, ?)",
            (replay_id, ar.model_dump_json()),
        )
        self._conn.commit()

    def get_attribution(self, replay_id: str) -> AttributionResult | None:
        row = self._conn.execute(
            "SELECT payload FROM attribution_results WHERE replay_id=?", (replay_id,)
        ).fetchone()
        return AttributionResult.model_validate_json(row["payload"]) if row else None

    # ---- Pattern 规律库（Phase 2）----

    def save_pattern(self, pattern: Pattern) -> None:
        """幂等：source_replay_id 为 PK，重跑覆盖。embedding 单独存 BLOB。"""
        emb = pattern.embedding
        payload = pattern.model_copy(update={"embedding": None}).model_dump_json()
        emb_blob = np.asarray(emb, dtype=np.float32).tobytes() if emb else None
        self._conn.execute(
            "INSERT OR REPLACE INTO patterns"
            "(source_replay_id, payload, embedding, embedding_model, shock_dimension, impact_direction, industries, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                pattern.source_replay_id,
                payload,
                emb_blob,
                pattern.embedding_model,
                pattern.shock_dimension.value,
                pattern.impact_direction.value,
                ",".join(pattern.industries),
                pattern.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_pattern(self, source_replay_id: str) -> Pattern | None:
        row = self._conn.execute(
            "SELECT payload, embedding, embedding_model FROM patterns WHERE source_replay_id=?",
            (source_replay_id,),
        ).fetchone()
        if not row:
            return None
        p = Pattern.model_validate_json(row["payload"])
        if row["embedding"]:
            emb = np.frombuffer(row["embedding"], dtype=np.float32).tolist()
            p = p.model_copy(update={"embedding": emb, "embedding_model": row["embedding_model"]})
        return p

    def search_patterns(
        self,
        query_vec: list[float] | None,
        *,
        shock_dimension: Any = None,
        impact_direction: Any = None,
        industry: str | None = None,
        top_k: int = 5,
    ) -> list[tuple[Pattern, float]]:
        """向量 cosine + 结构化过滤（枚举 + 子串）。query_vec=None 时仅结构化（embedding 不要求）。"""
        where: list[str] = []
        params: list[Any] = []
        if query_vec is not None:
            where.append("embedding IS NOT NULL")
        if shock_dimension is not None:
            where.append("shock_dimension=?")
            params.append(shock_dimension.value if hasattr(shock_dimension, "value") else shock_dimension)
        if impact_direction is not None:
            where.append("impact_direction=?")
            params.append(impact_direction.value if hasattr(impact_direction, "value") else impact_direction)
        if industry:
            where.append("industries LIKE ?")
            params.append(f"%{industry}%")
        sql = "SELECT source_replay_id, payload, embedding FROM patterns"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self._conn.execute(sql, params).fetchall()
        out: list[tuple[Pattern, float]] = []
        q = np.asarray(query_vec, dtype=np.float32) if query_vec is not None else None
        qn = float(np.linalg.norm(q)) if q is not None else 0.0
        for row in rows:
            p = Pattern.model_validate_json(row["payload"])
            if q is not None and row["embedding"]:
                emb = np.frombuffer(row["embedding"], dtype=np.float32)
                if emb.shape[0] == q.shape[0]:
                    en = float(np.linalg.norm(emb)) or 1.0
                    score = float(np.dot(q, emb) / (qn * en))
                else:
                    score = 0.0  # 维度不一致（embedding 后端切换过）：跳过向量打分而非崩溃
            else:
                score = 0.0
            out.append((p, score))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:top_k]

    def list_patterns(self) -> list[Pattern]:
        rows = self._conn.execute("SELECT source_replay_id FROM patterns").fetchall()
        return [self.get_pattern(r["source_replay_id"]) for r in rows]  # type: ignore[misc]

    # ---- 节点缓存 / 断点续跑 ----

    def get_cached_node(self, cache_key: str) -> dict[str, Any] | None:
        """缓存命中：返回已完成节点的 payload，否则 None。"""
        row = self._conn.execute(
            "SELECT payload FROM pipeline_runs WHERE cache_key=? AND status='done'", (cache_key,)
        ).fetchone()
        return json.loads(row["payload"]) if row and row["payload"] else None

    def save_node_run(
        self,
        *,
        replay_id: str,
        node_id: str,
        cache_key: str,
        status: str,
        payload: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_runs"
            "(replay_id, node_id, cache_key, status, payload, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                replay_id,
                node_id,
                cache_key,
                status,
                payload_json,
                (started_at or datetime.utcnow()).isoformat(),
                finished_at.isoformat() if finished_at else None,
            ),
        )
        self._conn.commit()

    def completed_nodes(self, replay_id: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT node_id FROM pipeline_runs WHERE replay_id=? AND status='done'", (replay_id,)
        ).fetchall()
        return {r["node_id"] for r in rows}

    # ---- skip_log ----

    def append_skip(
        self, *, replay_id: str, reason: str, stock_id: str | None = None, episode_id: str | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO skip_log(replay_id, stock_id, episode_id, reason, ts) VALUES (?, ?, ?, ?, ?)",
            (replay_id, stock_id, episode_id, reason, datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def get_skips(self, replay_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT stock_id, episode_id, reason, ts FROM skip_log WHERE replay_id=? ORDER BY id",
            (replay_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- seen_events（Phase 4 事件去重）----

    def is_seen(self, dedup_key: str, purpose: str | None = None) -> bool:
        if purpose is None:
            row = self._conn.execute(
                "SELECT 1 FROM seen_events WHERE dedup_key=?", (dedup_key,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT 1 FROM seen_events WHERE dedup_key=? AND purpose=?", (dedup_key, purpose)
            ).fetchone()
        return row is not None

    def mark_seen(self, dedup_key: str, source: str = "", payload: str = "", purpose: str = "") -> None:
        """幂等：INSERT OR IGNORE，(dedup_key, purpose) 复合主键，重复不覆盖。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_events(dedup_key, source, payload, seen_at, purpose) "
            "VALUES (?, ?, ?, ?, ?)",
            (dedup_key, source, payload, datetime.utcnow().isoformat(), purpose),
        )
        self._conn.commit()
