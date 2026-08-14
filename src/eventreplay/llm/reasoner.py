"""Reasoner：Claude Code CLI（headless）推理节点封装。

spike 确认（见 spike-results §3）：
- 调用必须 `--bare`（否则加载 .mcp.json 的 forge_extension MCP 会挂起）。
- `--json-schema` 提供 schema 强制力；`.structured_output` 为结构化结果字段。
- `--bare` 鉴权：GLM 网关用 ANTHROPIC_AUTH_TOKEN+BASE_URL（3P provider 凭证），直连用 ANTHROPIC_API_KEY。
- `--model` 接受 alias（sonnet 实跑可用）。

封装：Pydantic schema → 调用 → 读 .structured_output → Pydantic 校验 → 失败修复式重试 → LLMNodeFailed。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError


class LLMNodeFailed(Exception):
    """推理节点最终失败（重试耗尽）。"""


class Reasoner(Protocol):
    """推理节点接口：给定 system/user 提示与输出 schema，返回校验过的结构化对象。"""

    def call(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        model: str | None = None,
    ) -> BaseModel:
        ...

    def call_text(self, *, system: str, user: str, model: str | None = None) -> str:
        """纯文本输出调用（claude -p 默认 text 模式，无 JSON schema）。

        用于产物为大段 markdown 的节点（如 ReportNode），避免 JSON 转义/截断开销。
        """
        ...


class ClaudeCodeProvider:
    """Claude Code CLI headless 实现。"""

    def __init__(
        self,
        model_alias: str = "sonnet",
        cli_path: str = "claude",
        max_retries: int = 2,
        timeout_s: int = 300,
    ) -> None:
        self.model_alias = model_alias
        # 解析 CLI 全路径（Windows 上 npm 装的 claude.CMD 需 shutil.which 才能被 subprocess 找到）
        self.cli_path = shutil.which(cli_path) or cli_path
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        # 鉴权前提提示（不硬失败，让真实调用给出明确错误）
        if not (os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")):
            # 网关(ANTHROPIC_AUTH_TOKEN+BASE_URL) 或 直连(API_KEY) 至少一个
            pass

    def _build_args(self, schema_json: str, model: str) -> list[str]:
        # --bare 必须（避免 forge_extension MCP 挂起）；--disable-slash-commands 禁 skills
        return [
            self.cli_path,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
            "--bare",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
            "--model",
            model,
        ]

    def _invoke(self, system: str, user: str, schema_json: str, model: str) -> dict[str, Any]:
        prompt = f"{system}\n\n---\n\n{user}\n\n请严格按给定 JSON schema 输出，不要输出多余文字。"
        args = self._build_args(schema_json, model)
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            raise LLMNodeFailed(
                f"claude 退出码 {proc.returncode}: {proc.stderr.strip()[:300]}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMNodeFailed(
                f"claude stdout 非 JSON: {e}; stdout[:200]={proc.stdout[:200]!r}"
            ) from e

    def call(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        model: str | None = None,
    ) -> BaseModel:
        schema_json = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        use_model = model or self.model_alias
        last_err: str | None = None
        cur_user = user
        for attempt in range(self.max_retries + 1):
            try:
                envelope = self._invoke(system, cur_user, schema_json, use_model)
            except (subprocess.TimeoutExpired, LLMNodeFailed, OSError) as e:
                # 子进程超时/非零退出/启动失败同样占用重试名额（与 call_text 口径一致）
                last_err = f"{type(e).__name__}: {str(e)[:200]}"
                continue
            so = envelope.get("structured_output")
            if so is None:
                last_err = (
                    f"structured_output 缺失 (is_error={envelope.get('is_error')}, "
                    f"result={(envelope.get('result') or '')[:160]!r})"
                )
                cur_user = f"{user}\n\n[重试] 上次未返回结构化结果({last_err})，请严格按 JSON schema 输出。"
                continue
            try:
                return output_model.model_validate(so)
            except ValidationError as e:
                last_err = e.json()
                cur_user = (
                    f"{user}\n\n[重试] 上次输出 schema 校验失败：{e}. "
                    "请修正并严格按 JSON schema 输出。"
                )
                continue
        raise LLMNodeFailed(f"重试 {self.max_retries + 1} 次仍失败: {last_err}")

    def _build_text_args(self, model: str) -> list[str]:
        # text 模式：不加 --output-format json / --json-schema（claude -p 默认 text）
        return [
            self.cli_path,
            "-p",
            "--bare",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
            "--model",
            model,
        ]

    def call_text(self, *, system: str, user: str, model: str | None = None) -> str:
        """纯文本输出（claude -p 默认 text，无 JSON schema）。

        用于产物为大段 markdown 的节点（如 ReportNode），避免长 markdown 嵌 JSON 的
        转义/截断开销。失败按 max_retries 重试（网关偶发超时），耗尽抛 LLMNodeFailed。
        """
        use_model = model or self.model_alias
        prompt = f"{system}\n\n---\n\n{user}"
        args = self._build_text_args(use_model)
        last_err: str | None = None
        for _ in range(self.max_retries + 1):
            try:
                proc = subprocess.run(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    encoding="utf-8",
                )
            except subprocess.TimeoutExpired:
                last_err = f"claude 超时({self.timeout_s}s)"
                continue
            if proc.returncode != 0:
                last_err = f"claude 退出码 {proc.returncode}: {proc.stderr.strip()[:300]}"
                continue
            return proc.stdout
        raise LLMNodeFailed(f"call_text 重试 {self.max_retries + 1} 次仍失败: {last_err}")
