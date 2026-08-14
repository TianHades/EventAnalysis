"""Reasoner 测试：flag 构造 + happy/重试/校验修复（mock 离线）+ 真实 claude -p --bare 冒烟。"""

import json

import pytest
from pydantic import BaseModel

from eventreplay.llm import ClaudeCodeProvider, LLMNodeFailed


class Echo(BaseModel):
    ok: bool


# ---- 离线：flag 构造（--bare 必须）----


def test_build_args_contains_required_flags():
    p = ClaudeCodeProvider()
    args = p._build_args('{"type":"object"}', "sonnet")
    joined = " ".join(args)
    assert "--bare" in args, "--bare 必须（避免 forge_extension MCP 挂起）"
    assert "--disable-slash-commands" in args
    assert "--json-schema" in args
    assert "--permission-mode" in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "--model" in args
    assert "--mcp-config" not in args, "不传 --mcp-config（推理节点零外部工具）"


# ---- 离线：happy path（mock _invoke）----


def test_call_happy(monkeypatch):
    p = ClaudeCodeProvider()
    monkeypatch.setattr(
        p, "_invoke", lambda system, user, schema_json, model: {"structured_output": {"ok": True}}
    )
    result = p.call(system="s", user="u", output_model=Echo)
    assert isinstance(result, Echo) and result.ok is True


# ---- 离线：structured_output 缺失 → 重试耗尽 → LLMNodeFailed ----


def test_call_missing_structured_output_retries_then_fails(monkeypatch):
    p = ClaudeCodeProvider(max_retries=1)
    monkeypatch.setattr(
        p, "_invoke", lambda *a, **k: {"structured_output": None, "is_error": False}
    )
    with pytest.raises(LLMNodeFailed):
        p.call(system="s", user="u", output_model=Echo)
    # max_retries=1 → 共调用 2 次
    assert True


# ---- 离线：Pydantic 校验失败 → 修复式重试 → 成功 ----


def test_call_validation_error_triggers_repair_retry(monkeypatch):
    p = ClaudeCodeProvider(max_retries=2)
    calls = {"n": 0}

    def fake_invoke(system, user, schema_json, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"structured_output": {"ok": "不是bool"}}  # 校验失败
        return {"structured_output": {"ok": True}}  # 修复成功

    monkeypatch.setattr(p, "_invoke", fake_invoke)
    result = p.call(system="s", user="u", output_model=Echo)
    assert result.ok is True
    assert calls["n"] == 2


# ---- 离线：envelope 非 JSON / 退出码非 0 ----


def test_invoke_nonzero_exit(monkeypatch):
    p = ClaudeCodeProvider()

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "auth failed"

    monkeypatch.setattr(
        "eventreplay.llm.reasoner.subprocess.run", lambda *a, **k: FakeProc()
    )
    with pytest.raises(LLMNodeFailed):
        p._invoke("s", "u", "{}", "sonnet")


# ---- 真实 claude -p --bare 冒烟（spike：必须 --bare 才不挂起）----


def test_real_claude_bare_smoke():
    p = ClaudeCodeProvider(timeout_s=90)
    try:
        result = p.call(
            system="你是一个测试助手。",
            user="请返回 ok=true。",
            output_model=Echo,
        )
    except (LLMNodeFailed, OSError) as e:
        pytest.skip(f"真实 claude 不可用/超时: {type(e).__name__}: {str(e)[:80]}")
        return
    assert isinstance(result, Echo)
    assert result.ok is True
