"""Embedder 测试：三模式（auto/zhipu/local）+ mock + 缓存 + 失败→None + auto 兜底。

真实本地/智谱 smoke 见 test_real_smoke.py（避免每次单测加载模型/触网）。
"""

import pytest

from eventreplay.llm import Embedder


def _set_backend(monkeypatch, backend):
    if backend is None:
        monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    else:
        monkeypatch.setenv("EMBEDDING_BACKEND", backend)


def test_embedder_local_backend(monkeypatch):
    _set_backend(monkeypatch, "local")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = Embedder()
    assert e._backend == "local"
    assert e.embedding_dim == 512
    assert "bge-small-zh" in e.model_name


def test_embedder_zhipu_backend(monkeypatch):
    _set_backend(monkeypatch, "zhipu")
    monkeypatch.setenv("ZHIPU_API_KEY", "fake-zhipu")
    e = Embedder()
    assert e._backend == "zhipu"
    assert e.embedding_dim == 2048
    assert e.model_name == "embedding-3"


def test_embedder_empty_text_returns_none(monkeypatch):
    _set_backend(monkeypatch, "local")
    assert Embedder().embed("") is None


def test_embedder_cache_hit(monkeypatch):
    _set_backend(monkeypatch, "local")
    e = Embedder()
    calls = {"n": 0}

    def fake_call(text):
        calls["n"] += 1
        return [0.1] * e.embedding_dim

    monkeypatch.setattr(e, "_call", fake_call)
    v1 = e.embed("双减")
    v2 = e.embed("双减")
    assert calls["n"] == 1, "同文本第二次应命中缓存"
    assert v1 == v2 and len(v1) == e.embedding_dim


def test_embedder_failure_returns_none(monkeypatch):
    _set_backend(monkeypatch, "local")
    e = Embedder()

    def boom(text):
        raise RuntimeError("network")

    monkeypatch.setattr(e, "_call", boom)
    assert e.embed("x") is None


def test_embedder_wrong_dim_returns_none(monkeypatch):
    _set_backend(monkeypatch, "local")
    e = Embedder()
    monkeypatch.setattr(e, "_call", lambda t: [0.1] * 10)  # 维度不对
    assert e.embed("x") is None


def test_embedder_auto_no_token_locks_local(monkeypatch):
    """auto + 无任何在线 token → 首次 embed 锁定 local。"""
    _set_backend(monkeypatch, "auto")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = Embedder()
    monkeypatch.setattr(e, "_call_local", lambda t: [0.1] * 512)
    assert e.embed("x") is not None
    assert e._auto_resolved == "local"
    assert e.embedding_dim == 512


def test_embedder_auto_zhipu_ok_locks_zhipu(monkeypatch):
    """auto + 有 ZHIPU_API_KEY + 智谱可达 → 锁定 zhipu（在线优先）。"""
    _set_backend(monkeypatch, "auto")
    monkeypatch.setenv("ZHIPU_API_KEY", "fake-zhipu")
    e = Embedder()
    monkeypatch.setattr(e, "_call_zhipu", lambda t: [0.1] * 2048)
    assert e.embed("x") is not None
    assert e._auto_resolved == "zhipu"
    assert e.embedding_dim == 2048
    assert e.model_name == "embedding-3"


def test_embedder_auto_zhipu_fail_fallback_local(monkeypatch):
    """auto + 有 token 但智谱连不上 → 兜底 local（连不上用本地）。"""
    _set_backend(monkeypatch, "auto")
    monkeypatch.setenv("ZHIPU_API_KEY", "fake-zhipu")
    e = Embedder()

    def boom(text):
        raise RuntimeError("401")

    monkeypatch.setattr(e, "_call_zhipu", boom)
    monkeypatch.setattr(e, "_call_local", lambda t: [0.1] * 512)
    assert e.embed("x") is not None
    assert e._auto_resolved == "local", "智谱失败应兜底锁定 local"
    assert e.embedding_dim == 512


def test_embedder_auto_lock_does_not_switch(monkeypatch):
    """auto 锁定后不切换（避免规律库 dim 混乱）：锁定 local 后 zhipu 恢复也不切。"""
    _set_backend(monkeypatch, "auto")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = Embedder()
    monkeypatch.setattr(e, "_call_local", lambda t: [0.1] * 512)
    e.embed("x")  # 锁定 local
    assert e._auto_resolved == "local"
    # 之后即使 _call_zhipu 可用也不切换
    monkeypatch.setattr(e, "_call_zhipu", lambda t: [0.1] * 2048)
    e.embed("y")
    assert e._auto_resolved == "local"
    assert e.embedding_dim == 512
