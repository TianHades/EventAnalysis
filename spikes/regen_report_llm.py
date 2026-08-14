#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 SQLite 加载 Replay → 重算分级(car_best) → LLM 报告节点生成完整报告（600s 超时）。"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

from eventreplay.storage import Storage
from eventreplay.pipeline.nodes.attribution import AttributionNode
from eventreplay.pipeline.nodes.report import ReportNode
from eventreplay.llm import ClaudeCodeProvider

rid = sys.argv[1] if len(sys.argv) > 1 else "china_japan_dualuse_2026"
st = Storage("data/eventreplay.db")
event = st.get_event(rid)
chain = st.get_chain(rid)
ms = st.get_measurements(rid)
if event is None:
    print(f"未找到 {rid}"); sys.exit(1)
print(f"事件: {event.description[:50]}  度量: {len(ms)} 只")

# 重算分级（car_best）
candidates = [{"stock_id": m.stock_id, "direction": m.direction} for m in ms if m.direction]
attr = AttributionNode().run({"event": event, "candidates": candidates, "measurements": ms})
print(f"受益池: {len(attr.beneficiary_pool)}  受损池: {len(attr.victim_pool)}")

# 三级行业标签（从 tushare 反向映射，零网络）
from eventreplay.adapters.tushare_source import TushareSource
ts = TushareSource()
tagged = sum(1 for m in ms if ts.classify(m.stock_id))
for m in ms:
    m.sw_industry_l3 = ts.classify(m.stock_id)
print(f"三级行业标签: {tagged}/{len(ms)} 命中")

# LLM 报告（600s 超时，给 GLM 足够时间产完整叙述）
print("调 LLM 报告节点（timeout=600s）...")
reasoner = ClaudeCodeProvider(model_alias="sonnet", timeout_s=600)
node = ReportNode(reasoner)
md = node.run({"event": event, "chain": chain, "attribution_result": attr, "measurements": ms})

# 检测是否走了模板兜底
is_template = "模板生成" in md
print(f"报告生成完成: {'⚠️模板兜底' if is_template else '✅ LLM 完整报告'} ({len(md)} 字符)")

out = Path(f"reports/{rid}.md")
out.write_text(md, encoding="utf-8")
print(f"已写入: {out}")
