#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重度量（双层基准）+ 重生成报告：从已存 Replay 的候选重算 CAR(市场)+CAR(行业)，再 LLM 报告。"""
import sys
from datetime import timedelta
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()  # 必须：加载 .env 里的 TUSHARE_TOKEN

from eventreplay.storage import Storage
from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.adapters.tushare_source import TushareSource
from eventreplay.measurement.event_study import measure_stock
from eventreplay.pipeline.nodes.attribution import AttributionNode
from eventreplay.pipeline.nodes.report import ReportNode
from eventreplay.llm import ClaudeCodeProvider

rids = sys.argv[1:] or ["china_japan_dualuse_2026", "india_pakistan_2025"]
st = Storage("data/eventreplay.db")
qa = AkshareQuoteAdapter()
sm = StockMaster(); sm.ensure_loaded()
ts = TushareSource()
ind_codes = ts.industry_index_codes()
print(f"StockMaster: {len(sm._master)} 标的 | 行业指数映射: {len(ind_codes)} 个")

for rid in rids:
    event = st.get_event(rid)
    chain = st.get_chain(rid)
    old_ms = st.get_measurements(rid)
    if event is None:
        print(f"\n=== {rid}: 未找到 ==="); continue

    candidates = [{"stock_id": m.stock_id, "direction": m.direction} for m in old_ms if m.direction]
    print(f"\n=== {rid}: {event.description[:40]}... 候选 {len(candidates)} ===")

    start = event.reaction_date - timedelta(days=350)
    end = event.reaction_date + timedelta(days=200)
    bench = qa.index_daily("sh000300", start, end)

    new_ms = []
    for c in candidates:
        code = c["stock_id"]
        resolved = sm.resolve(code)
        if not resolved: continue
        try:
            q = qa.daily_adjusted(code, start, end)
        except Exception: continue
        # 行业指数（双层基准）
        bench_ind = None
        l1 = ts.classify_l1(code)
        if l1:
            ic = ind_codes.get(l1)
            if ic:
                bench_ind = ts.fetch_industry_index_daily(ic)
        m = measure_stock(
            stock_id=code, episode_id=f"{rid}-ep0", quotes=q, bench=bench,
            reaction_date=event.reaction_date, board=resolved.board,
            is_st=resolved.is_st, listing_date=resolved.listing_date,
            direction=c["direction"], bench_industry=bench_ind,
        )
        if m is not None:
            m.stock_name = resolved.name
            m.sw_industry_l3 = ts.classify(code)
            new_ms.append(m)

    # 行业调整 CAR（peer-adjusted：标的 CAR − 同 L1 行业候选池均值 = 板块内 alpha）
    from collections import defaultdict
    ind_cars = defaultdict(lambda: {"20": [], "60": [], "90": []})
    for m in new_ms:
        l1 = ts.classify_l1(m.stock_id)
        if l1:
            if m.car_0_20 is not None: ind_cars[l1]["20"].append(m.car_0_20)
            if m.car_0_60 is not None: ind_cars[l1]["60"].append(m.car_0_60)
            if m.car_0_90 is not None: ind_cars[l1]["90"].append(m.car_0_90)
    ind_avg = {l1: {w: sum(v)/len(v) if v else 0 for w, v in d.items()} for l1, d in ind_cars.items()}
    for m in new_ms:
        l1 = ts.classify_l1(m.stock_id)
        if l1 and l1 in ind_avg:
            m.car_industry_0_20 = (m.car_0_20 - ind_avg[l1]["20"]) if m.car_0_20 is not None else None
            m.car_industry_0_60 = (m.car_0_60 - ind_avg[l1]["60"]) if m.car_0_60 is not None else None
            m.car_industry_0_90 = (m.car_0_90 - ind_avg[l1]["90"]) if m.car_0_90 is not None else None

    ind_hit = sum(1 for m in new_ms if m.car_industry_0_20 is not None)
    print(f"重度量: {len(new_ms)}/{len(candidates)} | 行业调整命中: {ind_hit}/{len(new_ms)}")

    attr = AttributionNode().run({"event": event, "candidates": candidates, "measurements": new_ms})
    print(f"受益池: {len(attr.beneficiary_pool)}  受损池: {len(attr.victim_pool)}")

    print("调 LLM 报告节点（timeout=600s）...")
    reasoner = ClaudeCodeProvider(model_alias="sonnet", timeout_s=600)
    node = ReportNode(reasoner)
    md = node.run({"event": event, "chain": chain, "attribution_result": attr, "measurements": new_ms})
    is_template = "模板生成" in md
    out = Path(f"reports/{rid}.md")
    out.write_text(md, encoding="utf-8")
    print(f"{'⚠️模板' if is_template else '✅ LLM'} 报告 ({len(md)} 字符) → {out}")
