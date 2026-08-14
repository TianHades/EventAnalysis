#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试真实 smoke：跑 ingest→chain→candidate→impact，打印候选与度量/skip。"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
from datetime import date, timedelta
from pathlib import Path

cache = Path("cache/debug"); cache.mkdir(parents=True, exist_ok=True)
db = Path("cache/debug/t.db"); db.unlink(missing_ok=True)

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.llm import ClaudeCodeProvider
from eventreplay.pipeline.orchestrator import ReplayOrchestrator
from eventreplay.storage import Storage

qa = AkshareQuoteAdapter(cache_dir=cache / "q")
sm = StockMaster(cache_dir=cache / "sm"); sm.ensure_loaded()
print(f"StockMaster 加载 {len(sm._master)} 标的, 曾用名索引 {len(sm._name_index)}")
print(f"  resolve('豆神教育') ->", sm.resolve("豆神教育"))
print(f"  resolve('学大教育') ->", sm.resolve("学大教育"))
print(f"  resolve('300010') ->", sm.resolve("300010"))

storage = Storage(str(db))
reasoner = ClaudeCodeProvider(model_alias="sonnet", timeout_s=300)
orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, reports_dir=cache / "reports")

# 手动跑前几节点，打印中间产物
ctx = {"_replay_id": "dbg"}
event = orch.ingest.run_cached({"description":"双减政策落地，打压K12校外培训。","announcement_date":"2021-07-24","reaction_date":"2021-07-26","event_id":"dbg",**ctx})
print("\n[1] Event:", event.event_type, event.impact_direction)
chain_nodes = orch.chain.run_cached({"event":event,**ctx})
print(f"[2] Chain nodes: {len(chain_nodes)} ->", [(n.label, n.sw_industry_l1, n.shock_direction, n.low_confidence) for n in chain_nodes])
cands = orch.cand.run_cached({"event":event,"chain_nodes":chain_nodes,**ctx})
print(f"[3] Candidates: {len(cands)}")
for c in cands:
    print("    ", c)

# impact
start = event.reaction_date - timedelta(days=300); end = event.reaction_date + timedelta(days=150)
bench = qa.index_daily("sh000300", start, end)
print(f"[4] bench rows: {len(bench)}")
ms = orch.impact.run_cached({"event":event,"candidates":cands,"bench_df":bench,**ctx})
print(f"[5] Measurements: {len(ms)}")
for m in ms:
    print(f"    {m.stock_id} car_0_20={m.car_0_20} low={m.low_confidence}")
print("[5] skips:", storage.get_skips("dbg"))
