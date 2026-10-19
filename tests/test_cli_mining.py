"""mine-events CLI 降级路径 + `python -m eventreplay` 入口的回归测试。

对应修复：
- 阶段1 LLM 失败时 miner.mine 返回 paths={}，CLI 旧代码直接 result['paths']['final'] → KeyError
  （用户看到 traceback）；现在应输出 [fail] 友好提示并以非零退出码结束。
- `python -m eventreplay --help` 旧代码报 "No module named eventreplay.__main__"（缺 __main__.py）；
  现在应退出码 0 并列出全部 8 个子命令。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# historical_miner.mine 阶段1 失败时的降级返回结构（paths 为空 dict）
_MINE_FAILED = {"final": [], "raw_count": 0, "filtered_out": 0, "llm_rejected": 0, "paths": {}}


class _FakeReasoner:
    """占位 reasoner：本测试不触真实 LLM（不起子进程、不联网）。"""

    def __init__(self, *args, **kwargs) -> None:
        pass


class _FakeStorage:
    """占位 Storage：避免命令体创建真实 SQLite。"""

    def __init__(self, *args, **kwargs) -> None:
        pass


class _Stage1FailedMiner:
    """阶段1 LLM 失败的 miner：返回 paths={} 的降级结构。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def mine(self, **kwargs) -> dict:
        return dict(_MINE_FAILED)


class _SucceededMiner:
    """成功路径 miner：三段落盘路径齐全。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def mine(self, **kwargs) -> dict:
        return {
            "final": [],
            "raw_count": 3,
            "filtered_out": 1,
            "llm_rejected": 0,
            "paths": {
                "raw": "data/mining_raw_x.json",
                "filtered": "data/mining_filtered_x.json",
                "final": "data/mining_candidates_x.json",
            },
        }


def test_mine_events_stage1_failure_friendly_error(monkeypatch):
    """阶段1 LLM 失败 → [fail] 友好提示 + 非零退出（旧代码在此抛 KeyError）。"""
    from typer.testing import CliRunner

    from eventreplay import cli as cli_mod

    monkeypatch.setattr(cli_mod, "ClaudeCodeProvider", _FakeReasoner)
    monkeypatch.setattr(cli_mod, "Storage", _FakeStorage)
    monkeypatch.setattr(cli_mod, "HistoricalEventMiner", _Stage1FailedMiner)

    result = CliRunner().invoke(
        cli_mod.app, ["mine-events", "--start", "2023-01-01", "--end", "2023-12-31"]
    )
    assert not isinstance(result.exception, KeyError), "降级路径不应抛 KeyError（旧代码）"
    assert result.exit_code != 0, "阶段1 失败应以非零退出码结束"
    assert "[fail]" in result.output
    assert "阶段1" in result.output


def test_mine_events_success_path_unchanged(monkeypatch):
    """成功路径语义不变：打印 [done] 计数与三段文件路径，退出码 0。"""
    from typer.testing import CliRunner

    from eventreplay import cli as cli_mod

    monkeypatch.setattr(cli_mod, "ClaudeCodeProvider", _FakeReasoner)
    monkeypatch.setattr(cli_mod, "Storage", _FakeStorage)
    monkeypatch.setattr(cli_mod, "HistoricalEventMiner", _SucceededMiner)

    result = CliRunner().invoke(
        cli_mod.app, ["mine-events", "--start", "2023-01-01", "--end", "2023-12-31"]
    )
    assert result.exit_code == 0
    assert "[done]" in result.output
    assert "mining_candidates_x.json" in result.output
    assert "mining_raw_x.json" in result.output


def test_python_m_eventreplay_help_lists_8_commands():
    """`python -m eventreplay --help` 可用（__main__.py），退出码 0 且列出 8 个子命令。"""
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"  # 子进程 help 含中文，固定 utf-8 便于断言
    proc = subprocess.run(
        [sys.executable, "-m", "eventreplay", "--help"],
        cwd=repo_root,
        env=env,
        capture_output=True,
    )
    out = (
        proc.stdout.decode("utf-8", errors="replace")
        + proc.stderr.decode("utf-8", errors="replace")
    )
    assert proc.returncode == 0, out
    for cmd in (
        "replay",
        "extract-pattern",
        "find-similar",
        "refresh-sw",
        "forward",
        "mine-events",
        "batch-replay",
        "monitor",
    ):
        assert cmd in out
