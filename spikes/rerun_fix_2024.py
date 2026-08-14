"""修复 924/517 candidate=0：删 candidate+后续缓存 → 重跑 replay+extract。"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.llm import ClaudeCodeProvider, Embedder
from eventreplay.pipeline.orchestrator import ReplayOrchestrator
from eventreplay.pipeline.pattern_service import PatternService
from eventreplay.storage import Storage
import json


def main():
    storage = Storage("data/eventreplay.db")
    reasoner = ClaudeCodeProvider(model_alias="haiku")
    qa = AkshareQuoteAdapter()
    sm = StockMaster()
    embedder = Embedder()
    ps = PatternService(storage, reasoner, embedder)
    orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, market="A股")

    data = json.loads(open("data/mining_candidates_2024-01-01_2024-12-31.json", encoding="utf-8").read())
    for dt in ["2024-09-24", "2024-05-17"]:
        item = [x for x in data["final"] if x["announcement_date"] == dt][0]
        rid = "replay-" + item["dedup_key"][:12]
        # 删 candidate+后续节点缓存（保留 ingest+chain）
        storage._conn.execute(
            "DELETE FROM pipeline_runs WHERE replay_id=? AND node_id IN "
            "('candidate_generation','impact_measurement','attribution_filtering',"
            "'transmission_chain','report_assembly','pattern_extract')",
            (rid,),
        )
        storage._conn.commit()
        print(f"[fix] 删除 {rid} candidate+后续，重跑...", flush=True)
        try:
            orch.replay(
                description=item["description"],
                announcement_date=item["announcement_date"],
                reaction_date=item["announcement_date"],
                event_id=rid,
                benchmark="sh000300",
            )
            ps.extract_pattern(rid)
            ar = storage.get_attribution(rid)
            ms = storage.get_measurements(rid)
            print(f"  => 受益{len(ar.beneficiary_pool)} 受损{len(ar.victim_pool)} meas={len(ms)}", flush=True)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
