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
from pathlib import Path
from typing import Protocol

# auto 模式锁定结果的持久化路径（跨进程一致：防进程 A 锁 zhipu(2048) 建库、
# 进程 B 重新探测锁 local(512) 后维度漂移）。测试可传入独立路径覆盖。
_DEFAULT_BACKEND_CACHE = Path("cache") / "embedder_backend.txt"

_RETRY_ATTEMPTS = 3  # 网络类瞬时异常总尝试次数（1 次原始 + 2 次重试）


class EmbedderProtocol(Protocol):
    def embed(self, text: str) -> list[float] | None: ...


class Embedder(EmbedderProtocol):
    EMBEDDING_DIM = 512  # bge-small-zh-v1.5（类属性，测试用）
    LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
    ZHIPU_DIM = 2048
    ZHIPU_MODEL = "embedding-3"
    ZHIPU_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/embeddings"

    def __init__(self, timeout: int = 30, backend_cache: str | Path | None = None) -> None:
        self._backend = os.getenv("EMBEDDING_BACKEND", "auto").lower()
        self.timeout = timeout
        self._cache: dict[str, list[float]] = {}
        self._model = None  # 本地模型懒加载
        self._zhipu_token = (
            os.getenv("ZHIPU_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        self._backend_cache = Path(backend_cache) if backend_cache is not None else _DEFAULT_BACKEND_CACHE
        # auto：先读持久化锁定值（跨进程一致）
        self._auto_resolved: str | None = (
            self._read_persisted_backend() if self._backend == "auto" else None
        )

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
        """文本 → 向量；空文本/失败/维度不符返回 None。

        网络类瞬时异常重试 2 次（共 3 次尝试）后仍失败才返回 None。
        """
        if not text:
            return None
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self._cache:
            return self._cache[key]
        vec: list[float] | None = None
        for _ in range(_RETRY_ATTEMPTS):
            try:
                vec = self._call(text)
                break
            except Exception:
                continue  # 瞬时网络异常：重试（耗尽后 vec 保持 None）
        if vec is None or len(vec) != self.embedding_dim:
            return None
        self._cache[key] = vec
        return vec

    def _read_persisted_backend(self) -> str | None:
        """读持久化的 auto 锁定值；文件缺失/非法值返回 None（重新探测）。"""
        try:
            v = self._backend_cache.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return None
        return v if v in ("zhipu", "local") else None

    def _persist_backend(self, backend: str) -> None:
        """auto 锁定结果写盘（best-effort：写失败不影响本次 embed）。

        临时文件 + os.replace 原子替换：多进程并发首探时不会读到半截内容，
        也降低互相覆盖出脏值的窗口。
        """
        try:
            import os

            self._backend_cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._backend_cache.with_suffix(".tmp")
            tmp.write_text(backend, encoding="utf-8")
            os.replace(tmp, self._backend_cache)
        except OSError:
            pass

    def _call(self, text: str) -> list[float]:
        """统一入口（测试 mock 此方法）。"""
        if self._backend == "zhipu":
            return self._call_zhipu(text)
        if self._backend == "local":
            return self._call_local(text)
        # auto：已锁定（内存或持久化文件）则用锁定值；未锁定首次探测并持久化
        if self._auto_resolved is None:
            if self._zhipu_token:
                try:
                    v = self._call_zhipu(text)
                    if v and len(v) == self.ZHIPU_DIM:
                        self._lock_backend("zhipu")
                        return v
                except Exception:
                    pass
            self._lock_backend("local")
        if self._auto_resolved == "zhipu":
            return self._call_zhipu(text)
        return self._call_local(text)

    def _lock_backend(self, backend: str) -> None:
        """锁定 auto 后端并持久化（跨进程一致，防维度漂移）。"""
        self._auto_resolved = backend
        self._persist_backend(backend)

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
