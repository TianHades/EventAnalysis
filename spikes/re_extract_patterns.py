"""定性复核：重提取全部存量 Pattern（基于修正后的度量与新池统计口径）。

- 读取：Storage（步骤 2 重算后的 measurements/attribution），不触碰 report md
- 写入：仅 patterns 表（source_replay_id 主键幂等覆盖）+ pipeline_runs 节点缓存
- 产出：5 维定性标签的前后翻转对比（magnitude/scope/persistence/significance/surprise）

用法：
  PYTHONPATH=src python spikes/re_extract_patterns.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, "src")
from dotenv import load_dotenv

load_dotenv(".env", override=True)

from eventreplay.llm import ClaudeCodeProvider, Embedder  # noqa: E402
from eventreplay.pipeline.pattern_service import PatternService  # noqa: E402
from eventreplay.storage import Storage  # noqa: E402

DB = "data/eventreplay.db"
DIMS = ("magnitude", "scope", "persistence", "significance", "surprise")


def main() -> None:
    model = os.getenv("EVENTREPLAY_MODEL", "sonnet")
    bak = f"{DB}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(DB, bak)
    print(f"[reextract] DB 已备份 → {bak}")

    storage = Storage(DB)
    reasoner = ClaudeCodeProvider(model_alias=model)
    embedder = Embedder()
    svc = PatternService(storage, reasoner, embedder)

    rids = [r[0] for r in storage._conn.execute("SELECT source_replay_id FROM patterns")]
    print(f"[reextract] {len(rids)} 个 pattern，model={model}")

    before: dict[str, dict | None] = {}
    for rid in rids:
        p = storage.get_pattern(rid)
        before[rid] = p.qualitative.model_dump() if p and p.qualitative else None

    ok = fail = no_emb = 0
    flips: dict[str, list[str]] = {d: [] for d in DIMS}
    t_start = time.time()
    for i, rid in enumerate(rids, 1):
        t0 = time.time()
        try:
            p = svc.extract_pattern(rid)
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(rids)}] {rid} FAIL {type(e).__name__}: {str(e)[:120]}", flush=True)
            continue
        if p is None:
            fail += 1
            print(f"[{i}/{len(rids)}] {rid} FAIL loader 返回 None", flush=True)
            continue
        ok += 1
        if not p.embedding:
            no_emb += 1
        new_q = p.qualitative.model_dump() if p.qualitative else None
        notes = []
        for d in DIMS:
            ov = (before[rid] or {}).get(d)
            nv = (new_q or {}).get(d)
            if ov != nv:
                flips[d].append(f"{rid}: {ov}→{nv}")
                notes.append(f"{d} {ov}→{nv}")
        tail = f" | 翻转: {'; '.join(notes)}" if notes else ""
        print(f"[{i}/{len(rids)}] {rid} ok ({time.time()-t0:.0f}s, emb={'y' if p.embedding else 'n'}){tail}", flush=True)

    print(f"\n===== 汇总（总耗时 {(time.time()-t_start)/60:.1f} 分钟）=====")
    print(f"成功 {ok}，失败 {fail}，embedding 缺失 {no_emb}")
    for d in DIMS:
        line = f"{d}: {len(flips[d])} 处翻转"
        if flips[d]:
            line += f"；示例: {flips[d][:3]}"
        print(line)
    storage.close()


if __name__ == "__main__":
    main()
