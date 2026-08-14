"""一次性脚本：用快修后的 render_report（narrative[:6000]）重新渲染 2016 这 10 份兜底报告。

背景：batch-replay 时 report_assembly 的 LLM 全超时 → Python 模板兜底，旧模板 narrative[:2000]
把 ③/④ 逻辑链叙述截断。chain.narrative 本身完整（2000-4000 字），快修 [:6000] 后重渲染即可
补全叙述，无需重跑 LLM（report LLM 仍会网关超时）。

数据来源：storage 的 chains/attribution_results/measurements/events 表（完整产物）。
用法：PYTHONPATH=src python spikes/rerender_2016_reports.py
"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

from __future__ import annotations

from pathlib import Path

from eventreplay.measurement.report_template import render_report
from eventreplay.storage import Storage

# 10 个 2016 事件 dedup_key 前 12 位（replay-{d}.md / evt-{d}）
DEDUPS = [
    "c6567d31cd49",  # 供给侧改革
    "5d62fb2d1e89",  # AlphaGo
    "fc228be93392",  # L 型定调
    "3bca8c1df810",  # 英国脱欧
    "13cc4b1bd6b3",  # 南海仲裁
    "7574850d6f4f",  # 朝鲜核试
    "675ed358ad3f",  # 930 限购
    "2a04168ec5e8",  # 特朗普当选
    "694870c8d1c5",  # OPEC 减产
    "51d1108c4b8f",  # 美联储加息
]

OUT_DIR = Path("reports")


def main() -> None:
    storage = Storage("data/eventreplay.db")
    ok = miss = 0
    for d in DEDUPS:
        rid = f"replay-{d}"
        event = storage.get_event(rid)  # orchestrator 用 event_id(=replay-{dedup}) 作 event.id
        chain = storage.get_chain(rid)  # chain.event_id 同样 = replay-{dedup}
        ar = storage.get_attribution(rid)
        ms = storage.get_measurements(rid)
        if not (event and ar and ms):
            print(f"SKIP {rid}: 缺产物 (event={bool(event)} chain={bool(chain)} attr={bool(ar)} meas={len(ms)})")
            miss += 1
            continue
        md = render_report(event=event, chain=chain, attribution=ar, measurements=ms)
        (OUT_DIR / f"{rid}.md").write_text(md, encoding="utf-8")
        nar = len(chain.narrative) if chain and chain.narrative else 0
        print(f"OK   {rid}: report={len(md)}B narrative={nar} 受益={len(ar.beneficiary_pool)} 受损={len(ar.victim_pool)} 标的={len(ms)}")
        ok += 1
    print(f"\n[done] 重渲染 {ok}/10（缺产物 {miss}）→ {OUT_DIR}/")


if __name__ == "__main__":
    main()
