"""用新 l1 lookup 重跑所有 2016-2023 事件的 candidate+后续（增强 source 召回）。

source.lookup 加了 l1 行业名匹配后，chain 用 l1 名（房地产/非银金融等）也能命中成分股。
重跑 candidate（l1 生效）→ impact → report → extract。
用法：PYTHONPATH=src python -u spikes/rerun_l1_all.py
"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

import json

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.llm import ClaudeCodeProvider, Embedder
from eventreplay.pipeline.orchestrator import ReplayOrchestrator
from eventreplay.pipeline.pattern_service import PatternService
from eventreplay.storage import Storage


def main():
    storage = Storage("data/eventreplay.db")
    reasoner = ClaudeCodeProvider(model_alias="haiku")
    qa = AkshareQuoteAdapter()
    sm = StockMaster()
    embedder = Embedder()
    ps = PatternService(storage, reasoner, embedder)
    orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, market="A股")

    # 排除 2024 的 4 个（刚重跑）
    data24 = json.loads(open("data/mining_candidates_2024-01-01_2024-12-31.json", encoding="utf-8").read())
    exclude = {"replay-" + item["dedup_key"][:12] for item in data24["final"]}

    # 所有 patterns 的 source_replay_id（replay- 前缀），排除 2024
    rids = [
        r[0] for r in storage._conn.execute(
            "SELECT source_replay_id FROM patterns WHERE source_replay_id LIKE 'replay-%'"
        ).fetchall()
        if r[0] not in exclude
    ]
    total = len(rids)
    print(f"[l1-rerun] 重跑 {total} 个事件（新 l1 lookup）...", flush=True)

    ok = fail = 0
    for i, rid in enumerate(rids, 1):
        event = storage.get_event(rid)
        if event is None:
            print(f"[{i}/{total}] SKIP {rid}（event 不在 storage）", flush=True)
            continue
        # 删 candidate+后续（保留 ingest+chain）
        storage._conn.execute(
            "DELETE FROM pipeline_runs WHERE replay_id=? AND node_id IN "
            "('candidate_generation','impact_measurement','attribution_filtering',"
            "'transmission_chain','report_assembly','pattern_extract')",
            (rid,),
        )
        storage._conn.commit()
        try:
            orch.replay(
                description=event.description,
                announcement_date=str(event.announcement_date),
                reaction_date=str(event.reaction_date),
                event_id=rid,
                benchmark="sh000300",
            )
            ps.extract_pattern(rid)
            ar = storage.get_attribution(rid)
            ms = storage.get_measurements(rid)
            nb = len(ar.beneficiary_pool) if ar else 0
            nv = len(ar.victim_pool) if ar else 0
            print(f"[{i}/{total}] OK {rid} 受益{nb} 受损{nv} meas={len(ms)}", flush=True)
            ok += 1
        except Exception as e:
            print(f"[{i}/{total}] FAIL {rid}: {type(e).__name__}: {str(e)[:60]}", flush=True)
            fail += 1
    print(f"\n[done] OK={ok} FAIL={fail}", flush=True)


if __name__ == "__main__":
    main()
