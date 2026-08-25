"""HKStockMaster 测试：resolve 反查（代码/名称）+ is_real_at + constituents（mock akshare）。

mock 列名与 akshare 1.18 真实列对齐（见 stock_profile_em.py 源码）：
- stock_hk_company_profile_em：所属行业 / 公司介绍（无"公司上市日期"/"公司概况"）
- stock_hk_security_profile_em：上市日期（LISTING_DATE，证券资料接口）
"""

import pickle
from datetime import date

import pandas as pd
import pytest

from eventreplay.adapters.hk_stock_master import HKStockMaster


def _spot_df():
    return pd.DataFrame({
        "代码": ["00700", "09988", "00005"],
        "中文名称": ["腾讯控股", "阿里巴巴-SW", "汇丰控股"],
        "英文名称": ["TENCENT", "ALIBABA", "HSBC"],
    })


def _profile_df(industry="咨询服务"):
    """公司资料真实列：无上市日期列（上市日在证券资料接口）。"""
    return pd.DataFrame({
        "公司名称": ["腾讯控股"],
        "所属行业": [industry],
        "公司介绍": ["互联网"],
    })


def _security_df(listing="1999-11-23"):
    """证券资料真实列（含上市日期）。"""
    return pd.DataFrame({
        "证券代码": ["00700"],
        "证券简称": ["腾讯控股"],
        "上市日期": [listing],
    })


def _setup(monkeypatch):
    import akshare as ak

    monkeypatch.setattr(ak, "stock_hk_spot", lambda: _spot_df())
    monkeypatch.setattr(ak, "stock_hk_company_profile_em", lambda symbol: _profile_df())
    monkeypatch.setattr(ak, "stock_hk_security_profile_em", lambda symbol: _security_df())
    # mock 数据集只有 3 只，放宽空守卫阈值（守卫行为在下方专门测试）
    monkeypatch.setattr(HKStockMaster, "_MIN_MASTER_SIZE", 1)


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
    monkeypatch.setattr(ak, "stock_hk_security_profile_em", lambda symbol: _security_df())
    monkeypatch.setattr(HKStockMaster, "_MIN_MASTER_SIZE", 1)
    sm = HKStockMaster(cache_dir=tmp_path)
    sm.resolve("00700")
    sm.resolve("00700")
    assert calls["n"] == 1  # profile 缓存


# ---- 守卫 / 永不抛契约 ----


def _raise():
    raise ConnectionError("网络不可达")


def test_resolve_network_failure_returns_none(monkeypatch, tmp_path):
    """首次加载网络断 → resolve 返回 None（永不抛契约，不炸整条 replay）。"""
    import akshare as ak

    monkeypatch.setattr(ak, "stock_hk_spot", _raise)
    sm = HKStockMaster(cache_dir=tmp_path)
    assert sm.resolve("00700") is None
    assert sm.is_real_at("00700", date(2023, 1, 1)) is False


def test_all_sources_failure_raises(tmp_path, monkeypatch):
    """spot 源失败 → ensure_loaded 抛错（重试耗尽传播原异常；不落盘空库）。"""
    import akshare as ak

    monkeypatch.setattr(ak, "stock_hk_spot", _raise)
    sm = HKStockMaster(cache_dir=tmp_path)
    with pytest.raises((ConnectionError, RuntimeError)):
        sm.ensure_loaded()
    assert list(tmp_path.glob("*.pkl")) == []  # 空库不落盘


def test_small_master_raises(tmp_path, monkeypatch):
    """主数据 < 500 只 → 抛错而非静默半库（用默认阈值 500）。"""
    import akshare as ak

    monkeypatch.setattr(ak, "stock_hk_spot", lambda: _spot_df())  # 仅 3 只
    sm = HKStockMaster(cache_dir=tmp_path)
    with pytest.raises(RuntimeError):
        sm.ensure_loaded()


def test_empty_cache_treated_as_miss(tmp_path, monkeypatch):
    """缓存解出的 _master 为空 → 视为 miss（删缓存重新拉取）。"""
    import akshare as ak

    sm0 = HKStockMaster(cache_dir=tmp_path)
    sm0._cache_path().write_bytes(pickle.dumps(({}, {})))  # 篡改：空主数据缓存

    calls = {"n": 0}

    def fake_spot():
        calls["n"] += 1
        return pd.DataFrame({
            "代码": [str(i).zfill(5) for i in range(1, 601)],
            "中文名称": [f"公司{i}" for i in range(1, 601)],
            "英文名称": [f"CO{i}" for i in range(1, 601)],
        })

    monkeypatch.setattr(ak, "stock_hk_spot", fake_spot)
    sm = HKStockMaster(cache_dir=tmp_path)
    sm.ensure_loaded()
    assert calls["n"] >= 1  # 触发重新拉取
    assert len(sm._master) == 600
    assert sm._loaded is True


def test_security_profile_failure_degrades(monkeypatch, tmp_path):
    """证券资料接口失败 → listing_date 退化为 None（resolve 仍成功）。"""
    import akshare as ak

    def _sec_raise(symbol):
        raise ConnectionError("网络不可达")

    monkeypatch.setattr(ak, "stock_hk_spot", lambda: _spot_df())
    monkeypatch.setattr(ak, "stock_hk_company_profile_em", lambda symbol: _profile_df())
    monkeypatch.setattr(ak, "stock_hk_security_profile_em", _sec_raise)
    monkeypatch.setattr(HKStockMaster, "_MIN_MASTER_SIZE", 1)
    sm = HKStockMaster(cache_dir=tmp_path)
    r = sm.resolve("00700")
    assert r is not None
    assert r.industry == "咨询服务"
    assert r.listing_date is None
