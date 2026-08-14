"""验证 report 精简后的生成时间：重跑英国脱欧（之前 FALLBACK），计时 + 字数 + banner。

system_prompt 改为"不重写 chain_narrative，输出<5000字"后，预期生成时间显著下降。
用法：PYTHONPATH=src python spikes/verify_report_fast.py
"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

import time
from pathlib import Path

from eventreplay.llm import ClaudeCodeProvider
from eventreplay.pipeline.nodes.report import ReportNode
from eventreplay.storage import Storage

RID = "replay-3bca8c1df810"  # 英国脱欧（text 模式曾 FALLBACK）


def main() -> None:
    storage = Storage("data/eventreplay.db")
    reasoner = ClaudeCodeProvider(model_alias="haiku")
    node = ReportNode(reasoner, storage)
    event = storage.get_event(RID)
    chain = storage.get_chain(RID)
    ar = storage.get_attribution(RID)
    ms = storage.get_measurements(RID)
    t0 = time.time()
    md = node.run(
        {"event": event, "chain": chain, "attribution_result": ar,
         "measurements": ms, "_replay_id": RID}
    )
    dt = time.time() - t0
    Path(f"reports/{RID}.md").write_text(md, encoding="utf-8")
    banner = "模板生成" in md
    print(f"=== 耗时 {dt:.0f}s | {len(md)}ch | 兜底banner={banner} ===", flush=True)
    print(md[:600], flush=True)


if __name__ == "__main__":
    main()
