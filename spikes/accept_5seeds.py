#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5 种子事件方法论验收：代表标的 × 真实 sina 行情 × measure_stock，校验 CAR 方向。

不走 LLM（纯度量），直接验证事件研究法在真实历史数据上的方向正确性。
方向判定（宽松，spec §6）：受益 → car_0_20 或 car_0_60 任一 > 0；受损 → 任一 < 0。
贸易战短窗全市场暴跌为已知合理例外。
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from datetime import date, timedelta
from pathlib import Path

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter, to_sina_symbol
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.measurement.event_study import measure_stock
from eventreplay.schema import StockDirection

# (event_name, reaction_date, [(code, expected_direction, note), ...])
SEEDS = [
    ("贸易战 2018-03-23", date(2018,3,23), [
        ("600598", StockDirection.BENEFICIARY, "北大荒/农业受益(中期)"),
        ("002475", StockDirection.VICTIM, "立讯精密/电子苹果链受损"),
    ]),
    ("新冠 2020-02-03", date(2020,2,3), [
        ("300677", StockDirection.BENEFICIARY, "英科医疗/防护暴涨"),
        ("600029", StockDirection.VICTIM, "南方航空/出行受损"),
    ]),
    ("双减 2021-07-26", date(2021,7,26), [
        ("000526", StockDirection.VICTIM, "学大教育/K12受损"),
    ]),
    ("ChatGPT 2023-01-30", date(2023,1,30), [
        ("688256", StockDirection.BENEFICIARY, "寒武纪/算力"),
        ("300308", StockDirection.BENEFICIARY, "中际旭创/光模块"),
    ]),
    ("新国九条 2024-04-15", date(2024,4,15), [
        ("600900", StockDirection.BENEFICIARY, "长江电力/红利"),
    ]),
]

qa = AkshareQuoteAdapter(cache_dir=Path("cache/accept_q"))
sm = StockMaster(cache_dir=Path("cache/debug/sm")); sm.ensure_loaded()
print(f"StockMaster: {len(sm._master)} 标的\n")

results = []
for ev_name, rd, stocks in SEEDS:
    print(f"=== {ev_name} ===")
    start = rd - timedelta(days=350); end = rd + timedelta(days=150)
    bench = qa.index_daily("sh000300", start, end)
    for code, direction, note in stocks:
        r = sm.resolve(code)
        if r is None:
            print(f"  {code} resolve 失败"); continue
        try:
            q = qa.daily_adjusted(code, start, end)
        except Exception as e:
            print(f"  {code} 行情失败 {type(e).__name__}"); continue
        m = measure_stock(stock_id=code, episode_id="e0", quotes=q, bench=bench, reaction_date=rd,
                          board=r.board, is_st=r.is_st, listing_date=r.listing_date, direction=direction)
        if m is None:
            print(f"  {code} {note}: 度量返回 None（数据不足/新股）"); results.append((ev_name,code,direction,None,False,note)); continue
        car20 = m.car_0_20; car60 = m.car_0_60
        # 方向校验（宽松）：受益需 car20>0 或 car60>0；受损需 car20<0 或 car60<0
        if direction == StockDirection.BENEFICIARY:
            ok = (car20 is not None and car20 > 0) or (car60 is not None and car60 > 0)
        else:
            ok = (car20 is not None and car20 < 0) or (car60 is not None and car60 < 0)
        low = " [低置信]" if m.low_confidence else ""
        print(f"  {code} {r.name:8s} car[0,+20]={car20!s:>8} car[0,+60]={car60!s:>8} {'✓' if ok else '✗'}{low}  ({note})")
        results.append((ev_name, code, direction, car20, ok, note))

print("\n=== 验收汇总 ===")
n = len(results); passed = sum(1 for *_, ok, __ in results if ok)
for ev, code, d, car20, ok, note in results:
    print(f"  {'✓' if ok else '✗'} {ev:24s} {code} {d.value} car20={car20!s:>8}  {note}")
print(f"\n方向正确率：{passed}/{n}")
