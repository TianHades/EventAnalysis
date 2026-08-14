#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 SQLite 已存度量重算分级（car_best）+ 重生成报告（无需重跑流水线）。"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
from eventreplay.storage import Storage
from eventreplay.pipeline.nodes.attribution import AttributionNode
from eventreplay.measurement.report_template import render_report

rid = sys.argv[1] if len(sys.argv) > 1 else "china_japan_dualuse_2026"
st = Storage("data/eventreplay.db")
event = st.get_event(rid)
chain = st.get_chain(rid)
ms = st.get_measurements(rid)
if event is None:
    print(f"未找到 {rid}"); sys.exit(1)
print(f"事件: {event.description[:40]}  度量标的: {len(ms)}")

# candidates 代理：从 measurements.direction 重建
candidates = [{"stock_id": m.stock_id, "direction": m.direction} for m in ms if m.direction]

# 重算分级（用更新后的 car_best 逻辑）
attr = AttributionNode().run({"event": event, "candidates": candidates, "measurements": ms})
print(f"受益池: {len(attr.beneficiary_pool)}  受损池: {len(attr.victim_pool)}")

# 验证中船特气（仅中日事件）
zhong = [m for m in attr.beneficiary_pool if m.stock_id == "688146"]
if zhong:
    print(f"✓ 中船特气(688146) 在受益池: tier={zhong[0].tier.value}")
else:
    print("(该事件无 688146 或未入池)")

# 重生成报告
md = render_report(event=event, chain=chain, attribution=attr, measurements=ms)
out = Path(f"reports/{rid}.md")
out.write_text(md, encoding="utf-8")
print(f"报告已重生成: {out}  ({len(md)} 字符)")
