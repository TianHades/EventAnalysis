"""重渲染所有 replay 报告（text 模式 ## 1+## 4，## 4 定性含 surprise）。

从 attribution_results 读所有有产物的 replay，调 ReportNode.run（text 模式）重渲染。
用法：PYTHONPATH=src python -u spikes/rerun_report_text.py
"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

from pathlib import Path

from eventreplay.llm import ClaudeCodeProvider
from eventreplay.pipeline.nodes.report import ReportNode
from eventreplay.storage import Storage


def main() -> None:
    storage = Storage("data/eventreplay.db")
    reasoner = ClaudeCodeProvider(model_alias="haiku")
    node = ReportNode(reasoner, storage)
    rids = [r[0] for r in storage._conn.execute("SELECT replay_id FROM attribution_results").fetchall()]
    total = len(rids)
    print(f"重渲染 {total} 份报告...", flush=True)
    n_text = n_fb = n_err = 0
    for i, rid in enumerate(rids, 1):
        try:
            event = storage.get_event(rid)
            chain = storage.get_chain(rid)
            ar = storage.get_attribution(rid)
            ms = storage.get_measurements(rid)
            md = node.run(
                {"event": event, "chain": chain, "attribution_result": ar,
                 "measurements": ms, "_replay_id": rid}
            )
            Path(f"reports/{rid}.md").write_text(md, encoding="utf-8")
            fb = "模板生成" in md
            if fb:
                n_fb += 1
                tag = "FALLBACK"
            else:
                n_text += 1
                tag = "TEXT"
            print(f"[{i}/{total}] {tag} {rid} {len(md)}ch", flush=True)
        except Exception as e:
            n_err += 1
            print(f"[{i}/{total}] ERROR {rid}: {type(e).__name__}: {str(e)[:60]}", flush=True)
    print(f"\n[done] TEXT={n_text} FALLBACK={n_fb} ERROR={n_err}", flush=True)


if __name__ == "__main__":
    main()
