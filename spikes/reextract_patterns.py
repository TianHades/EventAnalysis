"""重提炼所有 Pattern（新 system_prompt 含事件级定性 4 维），验证 qualitative 落库。

prompt_hash 因 system_prompt + _PatternLLM schema 变 → 缓存自动失效 → 重 extract。
embedding 按 event_summary 文本 hash 缓存（不变则复用，不重算）。
用法：PYTHONPATH=src python -u spikes/reextract_patterns.py
"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

from eventreplay.llm import ClaudeCodeProvider, Embedder
from eventreplay.pipeline.pattern_service import PatternService
from eventreplay.storage import Storage


def main() -> None:
    storage = Storage("data/eventreplay.db")
    reasoner = ClaudeCodeProvider(model_alias="haiku")
    embedder = Embedder()
    svc = PatternService(storage, reasoner, embedder)

    rids = [r[0] for r in storage._conn.execute("SELECT source_replay_id FROM patterns").fetchall()]
    total = len(rids)
    print(f"重提炼 {total} 个 Pattern...", flush=True)
    ok = 0
    for i, rid in enumerate(rids, 1):
        try:
            p = svc.extract_pattern(rid)
        except Exception as e:  # 单个失败不中断
            print(f"[{i}/{total}] ERROR {rid[:24]}: {type(e).__name__}: {str(e)[:60]}", flush=True)
            continue
        if p and p.qualitative:
            q = p.qualitative
            print(
                f"[{i}/{total}] OK {rid[:24]} mag={q.magnitude} scope={q.scope} "
                f"per={q.persistence} sig={q.significance}",
                flush=True,
            )
            ok += 1
        else:
            print(f"[{i}/{total}] NO-QUAL {rid[:24]}", flush=True)
    print(f"[done] {ok}/{total} 有 qualitative", flush=True)


if __name__ == "__main__":
    main()
