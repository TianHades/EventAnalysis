"""--market 别名归一/校验的回归测试。

对应修复：--market 旧实现只认中文字面量"港股"，hk/HK 等别名会静默落入 A 股分支
（用错数据源）。现在 CLI 入口先归一别名，非法值在构造 LLM/适配器之前抛
typer.BadParameter（退出码 2，不触网、不构造 provider）。
"""

from __future__ import annotations

import pytest


def test_normalize_market_aliases():
    """A股/港股 别名（含大小写、空白）→ 规范中文值。"""
    from eventreplay.cli import normalize_market

    for raw in ("A股", "a", "A", "cn", "CN", "ashare", "AShare", "a-share", "  A股  "):
        assert normalize_market(raw) == "A股", raw
    for raw in ("港股", "hk", "HK", "hK", "hkex", "HKEX", "hongkong", "HongKong", " 港股 "):
        assert normalize_market(raw) == "港股", raw


@pytest.mark.parametrize("bad", ["us", "US", "美股", "", "  ", None, "a股h"])
def test_normalize_market_invalid_raises_value_error(bad):
    """未命中别名 → ValueError，且提示全部合法取值。"""
    from eventreplay.cli import normalize_market

    with pytest.raises(ValueError) as ei:
        normalize_market(bad)
    assert "合法取值" in str(ei.value)
    assert "港股" in str(ei.value)


def test_require_market_bad_parameter():
    """_require_market 把 ValueError 转成 typer.BadParameter（带 --market 参数提示）。"""
    import typer

    from eventreplay.cli import _require_market

    assert _require_market("hk") == "港股"
    assert _require_market("CN") == "A股"
    with pytest.raises(typer.BadParameter) as ei:
        _require_market("us")
    assert "合法取值" in str(ei.value)


def test_make_adapters_normalizes_market_alias(tmp_path, monkeypatch):
    """make_adapters 防御性归一：hk → 港股适配器 + HSI；A股 → A股适配器 + sh000300。"""
    monkeypatch.chdir(tmp_path)  # 适配器构造器会 mkdir 相对 cache 目录
    from eventreplay.adapters.hk_quote_adapter import HKQuoteAdapter
    from eventreplay.adapters.hk_stock_master import HKStockMaster
    from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
    from eventreplay.adapters.stock_master import StockMaster
    from eventreplay.cli import make_adapters

    qa, sm, bench = make_adapters("hk")
    assert isinstance(qa, HKQuoteAdapter)
    assert isinstance(sm, HKStockMaster)
    assert bench == "HSI"

    qa2, sm2, bench2 = make_adapters("A股")
    assert isinstance(qa2, AkshareQuoteAdapter)
    assert isinstance(sm2, StockMaster)
    assert bench2 == "sh000300"

    # 港股显式 benchmark 保留，A 股默认 benchmark 视为未指定 → 切 HSI
    assert make_adapters("HK", "HSI")[2] == "HSI"
    assert make_adapters("hk", "sh000300")[2] == "HSI"


@pytest.mark.parametrize(
    "args",
    [
        ["replay", "--desc", "x", "--date", "2024-01-15", "--market", "us"],
        ["forward", "--desc", "x", "--date", "2024-01-15", "--market", "us"],
        ["mine-events", "--start", "2024-01-01", "--end", "2024-01-31", "--market", "us"],
        ["batch-replay", "--job-file", "x.json", "--market", "us"],
    ],
    ids=["replay", "forward", "mine-events", "batch-replay"],
)
def test_invalid_market_rejected_before_llm(args, monkeypatch):
    """四个命令传 --market us → exit 2 + "合法取值"，且 ClaudeCodeProvider 未被构造。"""
    from typer.testing import CliRunner

    from eventreplay import cli as cli_mod

    class _BoomProvider:
        """被构造即报错：证明非法 --market 在构造 LLM 之前就失败。"""

        def __init__(self, *a, **k):
            raise AssertionError("非法 --market 不应构造 ClaudeCodeProvider")

    monkeypatch.setattr(cli_mod, "ClaudeCodeProvider", _BoomProvider)

    result = CliRunner().invoke(cli_mod.app, args)
    assert result.exit_code == 2, result.output
    assert "合法取值" in result.output
    assert not isinstance(result.exception, AssertionError), (
        "非法 --market 在构造 provider 前应已失败"
    )


def test_valid_market_alias_normalized_before_llm(monkeypatch):
    """合法别名在命令体内归一：replay --market hk 的日志显示 market=港股（不落 A 股分支）。"""
    from typer.testing import CliRunner

    from eventreplay import cli as cli_mod

    class _StopProvider:
        """构造后立刻抛错，避免进入真实流水线；用于断言归一发生在构造之前/输出中。"""

        def __init__(self, *a, **k):
            raise RuntimeError("stop-before-network")

    monkeypatch.setattr(cli_mod, "ClaudeCodeProvider", _StopProvider)

    result = CliRunner().invoke(
        cli_mod.app, ["replay", "--desc", "x", "--date", "2024-01-15", "--market", "hk"]
    )
    assert result.exit_code == 1  # provider 抛错被 CliRunner 捕获
    assert "market=港股" in result.output
