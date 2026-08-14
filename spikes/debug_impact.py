#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚焦验证：用真实候选代码直接跑 impact（无 LLM），确认 cleaning 修正后度量正常产出。"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from datetime import date, timedelta
from pathlib import Path

cache = Path("cache/debug")
from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.pipeline.nodes.impact import ImpactMeasurementNode
from eventreplay.schema import Event, StockDirection
from eventreplay.storage import Storage

qa = AkshareQuoteAdapter(cache_dir=cache / "q")
sm = StockMaster(cache_dir=cache / "sm"); sm.ensure_loaded()
storage = Storage(str(cache / "impact.db"))

# 双减事件
event = Event(id="dbg", description="双减", announcement_date=date(2021,7,24), reaction_date=date(2021,7,26), event_type="监管")

# debug 已产出的真实候选（取受损侧几只代表）
cands = [
    {"stock_id":"300010","name":"ST豆神","direction":StockDirection.VICTIM,"chain_node_id":None,"rationale":"K12大语文"},
    {"stock_id":"000526","name":"学大教育","direction":StockDirection.VICTIM,"chain_node_id":None,"rationale":"K12一对一"},
    {"stock_id":"600661","name":"昂立教育","direction":StockDirection.VICTIM,"chain_node_id":None,"rationale":"K12"},
    {"stock_id":"002607","name":"中公教育","direction":StockDirection.BENEFICIARY,"chain_node_id":None,"rationale":"职教转型"},
]

start = event.reaction_date - timedelta(days=300); end = event.reaction_date + timedelta(days=150)
bench = qa.index_daily("sh000300", start, end)
print(f"bench rows={len(bench)}")

node = ImpactMeasurementNode(qa, sm, storage)
ms = node.run({"event":event,"candidates":cands,"bench_df":bench,"_replay_id":"dbg"})
print(f"\nMeasurements: {len(ms)}/{len(cands)}")
for m in ms:
    print(f"  {m.stock_id} {m.direction.value} car[0,+20]={m.car_0_20!s:>8} low={m.low_confidence} tier={m.tier.value if m.tier else None}")
print("skips:", storage.get_skips("adhoc"))
