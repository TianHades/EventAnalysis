"""报告重生成：用修正后的度量/池统计重写 61 份复盘报告的 LLM 段。

- ## 1 摘要 + ## 4 判读（含"事件影响定性"）→ sonnet 重新生成（旧文本引用旧统计数字）
- ## 2/3/5/6/7 模板段 → render_report 用当前库内数据渲染
- LLM 失败的事件跳过、保留旧报告（不降级为纯模板）；不写 DB
- 跑前备份 reports/ 目录

用法：
  PYTHONPATH=src python spikes/regen_reports.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from dotenv import load_dotenv

load_dotenv(".env", override=True)

from eventreplay.llm import ClaudeCodeProvider  # noqa: E402
from eventreplay.measurement.report_template import render_report, report_filename  # noqa: E402
from eventreplay.pipeline.nodes.report import ReportNode  # noqa: E402
from eventreplay.storage import Storage  # noqa: E402

DB = "data/eventreplay.db"
REPORTS_DIR = "reports"


def main() -> None:
    model = os.getenv("EVENTREPLAY_MODEL", "sonnet")
    bak = f"{REPORTS_DIR}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(REPORTS_DIR, bak)
    print(f"[regen] reports 已备份 → {bak}", flush=True)

    storage = Storage(DB)
    reasoner = ClaudeCodeProvider(model_alias=model)
    node = ReportNode(reasoner)  # 复用 system_prompt/build_user

    rids = [r[0] for r in storage._conn.execute("SELECT DISTINCT replay_id FROM measurements ORDER BY replay_id")]
    print(f"[regen] {len(rids)} 份报告，model={model}", flush=True)

    ok = fail = 0
    t_start = time.time()
    for i, rid in enumerate(rids, 1):
        t0 = time.time()
        event = storage.get_event(rid)
        chain = storage.get_chain(rid)
        ar = storage.get_attribution(rid)
        ms = storage.get_measurements(rid)
        if event is None or ar is None:
            fail += 1
            print(f"[{i}/{len(rids)}] {rid} FAIL 库内缺少 event/attribution", flush=True)
            continue
        try:
            full = reasoner.call_text(system=node.system_prompt(), user=node.build_user({
                "event": event, "chain": chain, "attribution_result": ar, "measurements": ms,
            }))
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(rids)}] {rid} FAIL LLM {type(e).__name__}: {str(e)[:100]}（保留旧报告）", flush=True)
            continue
        if "===ANALYSIS===" in full:
            summary_md, analysis_md = full.split("===ANALYSIS===", 1)
            summary_md, analysis_md = summary_md.strip(), analysis_md.strip()
        else:
            summary_md, analysis_md = full.strip(), None
        md = render_report(
            event=event, chain=chain, attribution=ar, measurements=ms,
            summary_md=summary_md, analysis_md=analysis_md,
        )
        out_dir = Path(REPORTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{report_filename(event.description, str(event.announcement_date))}.md"
        out.write_text(md, encoding="utf-8")
        ok += 1
        print(f"[{i}/{len(rids)}] {rid} ok ({time.time()-t0:.0f}s) → {out.name}", flush=True)

    print(f"\n===== 汇总（{(time.time()-t_start)/60:.1f} 分钟）=====")
    print(f"重生成 {ok}，失败/跳过 {fail}（失败者保留旧报告）")
    storage.close()


if __name__ == "__main__":
    main()
