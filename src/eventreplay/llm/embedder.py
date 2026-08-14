"""Embedder：文本 → 向量（Pattern 检索用）。

三模式（`EMBEDDING_BACKEND` 环境变量，默认 `auto`）：
- `auto`（默认）：在线优先，连不上本地兜底。首次 embed 时探测：有 `ZHIPU_API_KEY` 且智谱可达
  → 锁定 zhipu（dim 2048）；否则锁定 local（dim 512）。锁定后不切换，避免规律库 dim 混乱。
- `zhipu`：强制智谱 embedding-3（dim 2048，需 `ZHIPU_API_KEY`），失败返回 None。
- `local`：强制本地 bge-small-zh-v1.5（dim 512，离线零 token）。

智谱 token 来源：`ZHIPU_API_KEY` 优先（与 Reasoner 的 `ANTHROPIC_AUTH_TOKEN` 分开，避免冲突）。
text-hash 缓存；失败→None（检索降级为纯结构化）。
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from typing import Protocol


class EmbedderProtocol(Protocol):
    def embed(self, text: str) -> list[float] | None: ...


class Embedder(EmbedderProtocol):
    EMBEDDING_DIM = 512  # bge-small-zh-v1.5（类属性，测试用）
    LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
    ZHIPU_DIM = 2048
    ZHIPU_MODEL = "embedding-3"
    ZHIPU_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/embeddings"

    def __init__(self, timeout: int = 30) -> None:
        self._backend = os.getenv("EMBEDDING_BACKEND", "auto").lower()
        self.timeout = timeout
        self._cache: dict[str, list[float]] = {}
        self._model = None  # 本地模型懒加载
        self._zhipu_token = (
            os.getenv("ZHIPU_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        self._auto_resolved: str | None = None  # auto 模式锁定的 backend

    @property
    def embedding_dim(self) -> int:
        if self._effective_backend() == "zhipu":
            return self.ZHIPU_DIM
        return self.EMBEDDING_DIM

    @property
    def model_name(self) -> str:
        return self.ZHIPU_MODEL if self._effective_backend() == "zhipu" else self.LOCAL_MODEL

    def _effective_backend(self) -> str:
        if self._backend == "zhipu":
            return "zhipu"
        if self._backend == "local":
            return "local"
        # auto：已锁定则用锁定值，未锁定默认 local（首次 _call 后才锁定）
        return self._auto_resolved or "local"

    def embed(self, text: str) -> list[float] | None:
        """文本 → 向量；空文本/失败/维度不符返回 None。"""
        if not text:
            return None
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self._cache:
            return self._cache[key]
        try:
            vec = self._call(text)
        except Exception:
            return None
        if vec is None or len(vec) != self.embedding_dim:
            return None
        self._cache[key] = vec
        return vec

    def _call(self, text: str) -> list[float]:
        """统一入口（测试 mock 此方法）。"""
        if self._backend == "zhipu":
            return self._call_zhipu(text)
        if self._backend == "local":
            return self._call_local(text)
        # auto：首次探测并锁定，之后不切换（避免 dim 混乱）
        if self._auto_resolved is None:
            if self._zhipu_token:
                try:
                    v = self._call_zhipu(text)
                    if v and len(v) == self.ZHIPU_DIM:
                        self._auto_resolved = "zhipu"
                        return v
                except Exception:
                    pass
            self._auto_resolved = "local"
        if self._auto_resolved == "zhipu":
            return self._call_zhipu(text)
        return self._call_local(text)

    def _call_local(self, text: str) -> list[float]:
        if self._model is None:
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 规避 HF 受限
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.LOCAL_MODEL)
        v = self._model.encode([text])[0]
        return [float(x) for x in v]

    def _call_zhipu(self, text: str) -> list[float]:
        import json

        if not self._zhipu_token:
            raise RuntimeError("zhipu backend 需 ZHIPU_API_KEY（或 ANTHROPIC_AUTH_TOKEN）")
        body = json.dumps({"model": self.ZHIPU_MODEL, "input": text}).encode("utf-8")
        req = urllib.request.Request(
            self.ZHIPU_ENDPOINT,
            data=body,
            headers={"Authorization": f"Bearer {self._zhipu_token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return list(data["data"][0]["embedding"])
