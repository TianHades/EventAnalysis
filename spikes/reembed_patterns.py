"""重嵌入所有 Pattern 为统一维度（bge-small-zh-v1.5，512 维）。

历史遗留：2023 Pattern 用 GLM embedding-3（2048 维），2016 用 bge（512 维），cosine 检索维度不匹配。
重嵌入：当前 Embedder（bge 512）重新 embed event_summary，更新 BLOB。
用法：PYTHONPATH=src python -u spikes/reembed_patterns.py
"""

from _loadenv import *  # noqa: F401  # 加载 .env（EMBEDDING_BACKEND 等）

import numpy as np

from eventreplay.llm import Embedder
from eventreplay.storage import Storage


def main() -> None:
    storage = Storage("data/eventreplay.db")
    emb = Embedder()
    print(f"Embedder: dim={Embedder.EMBEDDING_DIM} model={emb.model_name}", flush=True)
    import json

    rows = storage._conn.execute(
        "SELECT source_replay_id, payload, embedding, embedding_model FROM patterns"
    ).fetchall()
    reembedded = 0
    for rid, payload, blob, em_model in rows:
        summary = json.loads(payload).get("event_summary", "")
        cur_dim = len(np.frombuffer(blob, dtype=np.float32)) if blob else 0
        vec = emb.embed(summary)
        new_dim = len(vec) if vec else 0
        tag = ""
        if vec:
            new_blob = np.asarray(vec, dtype=np.float32).tobytes()
            storage._conn.execute(
                "UPDATE patterns SET embedding=?, embedding_model=? WHERE source_replay_id=?",
                (new_blob, emb.model_name, rid),
            )
            storage._conn.commit()
            reembedded += 1
            tag = " (updated)" if cur_dim != new_dim else ""
        print(
            f"  {rid[7:24]:17} {cur_dim}d->{new_dim}d  [{em_model}->{emb.model_name}]{tag}",
            flush=True,
        )
    print(f"[done] 重嵌入 {reembedded}/{len(rows)}（统一 {Embedder.EMBEDDING_DIM} 维）", flush=True)


if __name__ == "__main__":
    main()
