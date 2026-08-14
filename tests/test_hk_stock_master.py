"""HKStockMaster 测试：resolve 反查（代码/名称）+ is_real_at + constituents（mock akshare）。"""

from datetime import date

import pandas as pd

from eventreplay.adapters.hk_stock_master import HKStockMaster


def _spot_df():
    return pd.DataFrame({
        "代码": ["00700", "09988", "00005"],
        "中文名称": ["腾讯控股", "阿里巴巴-SW", "汇丰控股"],
        "英文名称": ["TENCENT", "ALIBABA", "HSBC"],
    })


def _profile_df(industry="咨询服务", listing="1999-11-23"):
    return pd.DataFrame({
        "公司名称": ["腾讯控股"],
        "所属行业": [industry],
        "公司上市日期": [listing],
        "公司概况": ["互联网"],
    })


def _setup(monkeypatch):
    import akshare as ak

    monkeypatch.setattr(ak, "stock_hk_spot", lambda: _spot_df())
    monkeypatch.setattr(ak, "stock_hk_company_profile_em", lambda symbol: _profile_df())


def test_resolve_by_code(monkeypatch, tmp_path):
    _setup(monkeypatch)
    sm = HKStockMaster(cache_dir=tmp_path)
    r = sm.resolve("00700")
    assert r is not None
    assert r.code == "00700"
    assert r.name == "腾讯控股"
    assert r.market == "港股"
    assert r.board == "港股主板"
    assert r.industry == "咨询服务"
    assert r.listing_date == date(1999, 11, 23)


def test_resolve_by_name(monkeypatch, tmp_path):
    _setup(monkeypatch)
    sm = HKStockMaster(cache_dir=tmp_path)
    assert sm.resolve("腾讯控股").code == "00700"
    assert sm.resolve("TENCENT").code == "00700"  # 英文名


def test_resolve_short_code_padded(monkeypatch, tmp_path):
    """短代码补 5 位：700 → 00700。"""
    _setup(monkeypatch)
    sm = HKStockMaster(cache_dir=tmp_path)
    assert sm.resolve("700").code == "00700"
    assert sm.resolve("hk00700").code == "00700"


def test_resolve_nonexistent(monkeypatch, tmp_path):
    _setup(monkeypatch)
    sm = HKStockMaster(cache_dir=tmp_path)
    assert sm.resolve("99999") is None
    assert sm.resolve("不存在的公司") is None


def test_is_real_at(monkeypatch, tmp_path):
    _setup(monkeypatch)
    sm = HKStockMaster(cache_dir=tmp_path)
    assert sm.is_real_at("00700", date(2023, 1, 1)) is True  # 上市后
    assert sm.is_real_at("00700", date(1999, 1, 1)) is False  # 上市前


def test_constituents_by_name_keyword(monkeypatch, tmp_path):
    _setup(monkeypatch)
    sm = HKStockMaster(cache_dir=tmp_path)
    codes = sm.constituents("腾讯")
    assert "00700" in codes
    assert sm.constituents("不存在") == []


def test_profile_cached(monkeypatch, tmp_path):
    """同一 code 多次 resolve 只 fetch profile 一次。"""
    import akshare as ak

    calls = {"n": 0}

    def fake_profile(symbol):
        calls["n"] += 1
        return _profile_df()

    monkeypatch.setattr(ak, "stock_hk_spot", lambda: _spot_df())
    monkeypatch.setattr(ak, "stock_hk_company_profile_em", fake_profile)
    sm = HKStockMaster(cache_dir=tmp_path)
    sm.resolve("00700")
    sm.resolve("00700")
    assert calls["n"] == 1  # profile 缓存
