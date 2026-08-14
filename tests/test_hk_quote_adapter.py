"""HKQuoteAdapter 测试：to_hk_symbol + mock akshare 日期过滤 + 真实 smoke。"""

from datetime import date

import pandas as pd
import pytest

from eventreplay.adapters.hk_quote_adapter import HKQuoteAdapter, to_hk_symbol


def test_to_hk_symbol():
    assert to_hk_symbol("00700") == "00700"
    assert to_hk_symbol("700") == "00700"  # 补 5 位
    assert to_hk_symbol("hk00700") == "00700"  # 去 hk 前缀
    assert to_hk_symbol("HK09988") == "09988"
    with pytest.raises(ValueError):
        to_hk_symbol("abc")


def _daily_df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2023-01-03", "2023-06-15", "2023-12-29"]).date,
        "open": [300, 320, 350], "high": [310, 330, 360],
        "low": [295, 315, 345], "close": [305, 325, 355], "volume": [1000, 1100, 1200],
    })


def test_daily_adjusted_filters_by_date(monkeypatch, tmp_path):
    import akshare as ak

    monkeypatch.setattr(ak, "stock_hk_daily", lambda **kw: _daily_df())
    qa = HKQuoteAdapter(cache_dir=tmp_path)
    df = qa.daily_adjusted("00700", date(2023, 3, 1), date(2023, 9, 1), adjust="hfq")
    assert len(df) == 1  # 只 2023-06-15 在范围
    assert df.iloc[0]["date"] == date(2023, 6, 15)


def test_index_daily_filters_by_date(monkeypatch, tmp_path):
    import akshare as ak

    idx_df = pd.DataFrame({
        "date": pd.to_datetime(["2023-01-03", "2023-06-15", "2023-12-29"]).date,
        "open": [20000, 21000, 22000], "high": [20100, 21100, 22100],
        "low": [19900, 20900, 21900], "close": [20050, 21050, 22050], "volume": [0, 0, 0],
    })
    monkeypatch.setattr(ak, "stock_hk_index_daily_sina", lambda symbol: idx_df)
    qa = HKQuoteAdapter(cache_dir=tmp_path)
    df = qa.index_daily("HSI", date(2023, 3, 1), date(2023, 9, 1))
    assert len(df) == 1
    assert df.iloc[0]["close"] == 21050


def test_daily_adjusted_uses_cache(monkeypatch, tmp_path):
    """第二次调用命中缓存，不重新 fetch。"""
    import akshare as ak

    calls = {"n": 0}

    def fake(**kw):
        calls["n"] += 1
        return _daily_df()

    monkeypatch.setattr(ak, "stock_hk_daily", fake)
    qa = HKQuoteAdapter(cache_dir=tmp_path)
    qa.daily_adjusted("00700", date(2023, 1, 1), date(2023, 12, 31))
    qa.daily_adjusted("00700", date(2023, 1, 1), date(2023, 6, 1))  # 第二次，不同日期范围
    assert calls["n"] == 1  # 命中缓存，未重新 fetch


@pytest.mark.skip(reason="真实网络 smoke，手动跑")
def test_real_smoke_hk_daily():
    qa = HKQuoteAdapter()
    df = qa.daily_adjusted("00700", date(2023, 1, 1), date(2023, 12, 31))
    assert len(df) > 100
    print(f"[smoke] 腾讯 2023 日线 {len(df)} 行")


@pytest.mark.skip(reason="真实网络 smoke，手动跑")
def test_real_smoke_hk_index():
    qa = HKQuoteAdapter()
    df = qa.index_daily("HSI", date(2023, 1, 1), date(2023, 12, 31))
    assert len(df) > 100
    print(f"[smoke] HSI 2023 {len(df)} 行")
