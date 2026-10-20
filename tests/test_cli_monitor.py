"""monitor --once 与默认常驻循环两条路径的 CLI 回归测试。

全部依赖 monkeypatch（cli 顶层导入 + 命令体函数内 import 的源模块属性），
不触网、不启真实 LLM、不写真实 DB。
"""

from __future__ import annotations


class _Fake:
    """通用占位：任意构造参数都接受（替代 provider/storage/embedder/channels 等）。"""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


class _FakePusher:
    """替代 MonitoringPusher：记录 run_once 调用，返回推送数 3。"""

    def __init__(self) -> None:
        self.run_once_calls: list[dict] = []

    def run_once(self, **kwargs) -> int:
        self.run_once_calls.append(kwargs)
        return 3


def _patch_monitor_deps(monkeypatch, fake_pusher) -> list[dict]:
    """替换 monitor 命令体的全部外部依赖，返回 run_monitor_loop 的调用记录。"""
    from eventreplay import cli as cli_mod
    from eventreplay.adapters import news_adapter as news_mod
    from eventreplay.pipeline import event_dedup as dedup_mod
    from eventreplay.pipeline import monitoring_pusher as pusher_mod
    from eventreplay.pipeline import push_channel as push_mod
    from eventreplay.pipeline.nodes import event_extractor as extractor_mod
    from eventreplay.pipeline.nodes import news_screening as screening_mod

    loop_calls: list[dict] = []

    def _fake_loop(pusher, **kwargs):
        loop_calls.append({"pusher": pusher, **kwargs})
        return None  # 真实循环为无限 while；测试中返回即收尾

    # cli 顶层 import 的符号
    monkeypatch.setattr(cli_mod, "ClaudeCodeProvider", _Fake)
    monkeypatch.setattr(cli_mod, "Storage", _Fake)
    monkeypatch.setattr(cli_mod, "Embedder", _Fake)
    monkeypatch.setattr(cli_mod, "PatternService", _Fake)
    monkeypatch.setattr(cli_mod, "ForwardOrchestrator", _Fake)
    monkeypatch.setattr(cli_mod, "make_adapters", lambda *a, **k: (_Fake(), _Fake(), "sh000300"))
    # monitor 命令体内 from ... import 的源模块属性
    for name in (
        "AkshareNewsAdapter",
        "CLSNewsAdapter",
        "CCTVNewsAdapter",
        "Jin10NewsAdapter",
        "JiuyanNewsAdapter",
        "MultiSourceNewsAdapter",
    ):
        monkeypatch.setattr(news_mod, name, _Fake)
    monkeypatch.setattr(dedup_mod, "EventDedup", _Fake)
    monkeypatch.setattr(pusher_mod, "MonitoringPusher", lambda *a, **k: fake_pusher)
    monkeypatch.setattr(pusher_mod, "run_monitor_loop", _fake_loop)
    monkeypatch.setattr(extractor_mod, "EventExtractor", _Fake)
    monkeypatch.setattr(screening_mod, "NewsScreeningNode", _Fake)
    for name in ("FileChannel", "WebhookChannel", "FeishuChannel"):
        monkeypatch.setattr(push_mod, name, _Fake)
    return loop_calls


def test_monitor_once_runs_single_round(monkeypatch):
    """--once：只调 pusher.run_once 一次、不进常驻循环，退出码 0 并输出推送条数。"""
    from typer.testing import CliRunner

    from eventreplay import cli as cli_mod

    fake_pusher = _FakePusher()
    loop_calls = _patch_monitor_deps(monkeypatch, fake_pusher)

    result = CliRunner().invoke(
        cli_mod.app,
        [
            "monitor",
            "--once",
            "--interval", "60",
            "--significance", "2",
            "--a-share", "3",
            "--surprise", "1.5",
            "--impact", "2",
            "--no-screen",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(fake_pusher.run_once_calls) == 1, "--once 应恰好跑一轮"
    assert not loop_calls, "--once 不应进入常驻循环"
    assert "--once 单轮完成" in result.output
    assert "3 条" in result.output
    # 阈值/开关原样透传给 run_once
    kwargs = fake_pusher.run_once_calls[0]
    assert kwargs["significance_min"] == 2
    assert kwargs["a_share_min"] == 3
    assert kwargs["surprise_min"] == 1.5
    assert kwargs["impact_min"] == 2
    assert kwargs["screen"] is False
    assert kwargs["echo"] is not None


def test_monitor_default_stays_in_loop(monkeypatch):
    """默认（无 --once）：进入 run_monitor_loop 一次，不直接调 run_once，行为不变。"""
    from typer.testing import CliRunner

    from eventreplay import cli as cli_mod

    fake_pusher = _FakePusher()
    loop_calls = _patch_monitor_deps(monkeypatch, fake_pusher)

    result = CliRunner().invoke(cli_mod.app, ["monitor", "--interval", "60"])
    assert result.exit_code == 0, result.output
    assert not fake_pusher.run_once_calls, "默认路径由 run_monitor_loop 驱动，不应直接调 run_once"
    assert len(loop_calls) == 1
    call = loop_calls[0]
    assert call["pusher"] is fake_pusher
    assert call["interval_min"] == 60
    # 默认阈值参数原样传入（不改常驻语义）
    assert call["significance_min"] == 3
    assert call["a_share_min"] == 4
    assert call["surprise_min"] == 3
    assert call["impact_min"] == 0
    assert call["screen"] is True
    assert call["echo"] is not None
