"""QuoteAdapter 测试：符号转换（离线）+ 缓存命中（mock）+ 真实 sina 拉取冒烟（网络失败 skip）。"""

from datetime import date

import pandas as pd
import pytest

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter, to_sina_symbol


# ---- 离线：符号转换 ----


@pytest.mark.parametrize(
    "code,expected",
    [
        ("600519", "sh600519"),  # 沪主板
        ("000001", "sz000001"),  # 深主板
        ("688256", "sh688256"),  # 科创板
        ("300308", "sz300308"),  # 创业板
        ("830799", "bj830799"),  # 北交所
        ("sh600519", "sh600519"),  # 已带前缀直通
    ],
)
def test_to_sina_symbol(code, expected):
    assert to_sina_symbol(code) == expected


def test_to_sina_symbol_invalid():
    with pytest.raises(ValueError):
        to_sina_symbol("abc123")
    with pytest.raises(ValueError):
        to_sina_symbol("12345")  # 非 6 位


# ---- 离线：缓存命中（mock fetcher）----


def _synthetic_daily(start: date, end: date) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame(
        {
            "date": [d.date() for d in dates],
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.0] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [1000.0] * len(dates),
            "amount": [10000.0] * len(dates),
            "outstanding_share": [1e8] * len(dates),
            "turnover": [0.001] * len(dates),
        }
    )


def test_daily_adjusted_cache_hit_avoids_refetch(tmp_path, monkeypatch):
    adapter = AkshareQuoteAdapter(cache_dir=tmp_path)
    calls = {"n": 0}

    def fake_fetch(self, sina_sym, start, end, adjust):
        calls["n"] += 1
        return _synthetic_daily(start, end)

    monkeypatch.setattr(AkshareQuoteAdapter, "_fetch_daily", fake_fetch)

    start, end = date(2024, 1, 2), date(2024, 1, 5)
    df1 = adapter.daily_adjusted("600519", start, end)
    df2 = adapter.daily_adjusted("600519", start, end)
    assert calls["n"] == 1, "第二次应命中缓存不再拉取"
    pd.testing.assert_frame_equal(df1, df2)
    assert "outstanding_share" in df1.columns


def test_index_daily_caches_full_then_filters(tmp_path, monkeypatch):
    adapter = AkshareQuoteAdapter(cache_dir=tmp_path)
    calls = {"n": 0}

    def fake_fetch_index(self, symbol):
        calls["n"] += 1
        dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 6, 30)]
        return pd.DataFrame(
            {"date": dates, "open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3, "close": [1.0] * 3, "volume": [0] * 3}
        )

    monkeypatch.setattr(AkshareQuoteAdapter, "_fetch_index", fake_fetch_index)

    sub = adapter.index_daily("sh000300", date(2024, 1, 1), date(2024, 1, 2))
    assert len(sub) == 2
    # 第二次（不同区间）应命中全量缓存，不再 fetch
    sub2 = adapter.index_daily("sh000300", date(2024, 6, 30), date(2024, 6, 30))
    assert calls["n"] == 1
    assert len(sub2) == 1


# ---- 真实 sina 拉取冒烟（网络不可达则 skip）----


def _skip_on_network(exc):
    pytest.skip(f"网络不可达，跳过真实拉取: {type(exc).__name__}")


def test_real_daily_adjusted_smoke(tmp_path):
    adapter = AkshareQuoteAdapter(cache_dir=tmp_path)
    try:
        df = adapter.daily_adjusted("600519", date(2024, 1, 2), date(2024, 1, 10))
    except ConnectionError as e:
        _skip_on_network(e)
        return
    assert not df.empty
    assert {"date", "close", "volume", "outstanding_share"} <= set(df.columns)
    # 涉及 spike 验证过的后复权高价股，close 应为正
    assert (df["close"] > 0).all()


def test_real_index_and_trade_dates_smoke(tmp_path):
    adapter = AkshareQuoteAdapter(cache_dir=tmp_path)
    try:
        idx = adapter.index_daily("sh000300", date(2024, 1, 2), date(2024, 1, 10))
        td = adapter.trade_dates()
    except ConnectionError as e:
        _skip_on_network(e)
        return
    assert not idx.empty and "close" in idx.columns
    assert len(td) > 1000  # 交易日历应有数千日
    assert td == sorted(td)
