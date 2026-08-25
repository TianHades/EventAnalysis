"""StockMaster 测试：resolve（代码/名称/曾用名/假代码）+ is_real_at + constituents（真实数据，网络失败 skip）。

离线段（mock _fetch_df，不触网）：空主数据守卫 / 空缓存视为 miss / 退市幸存者偏差修复。
"""

import pickle
from datetime import date

import pandas as pd
import pytest

from eventreplay.adapters.stock_master import StockMaster, infer_board


def _skip_on_network(exc):
    pytest.skip(f"网络不可达，跳过: {type(exc).__name__}: {str(exc)[:60]}")


@pytest.fixture(scope="module")
def master():
    """模块级共享，避免重复加载（真实拉取 ~4600 标的）。"""
    try:
        m = StockMaster(cache_dir="cache/stockmaster-test")
        m.ensure_loaded()
        if not m._master:
            pytest.skip("StockMaster 加载为空（网络不可达）")
        return m
    except (ConnectionError, RuntimeError) as e:
        _skip_on_network(e)


# ---- 离线：板块推断 ----


@pytest.mark.parametrize(
    "code,expected",
    [
        ("600519", "主板"),
        ("688256", "科创板"),
        ("300308", "创业板"),
        ("000001", "主板"),
        ("830799", "北交所"),
    ],
)
def test_infer_board(code, expected):
    assert infer_board(code) == expected


# ---- 真实数据：resolve ----


def test_resolve_by_code(master):
    r = master.resolve("600519")
    assert r is not None
    assert r.code == "600519"
    assert "茅台" in r.name


def test_resolve_by_name(master):
    r = master.resolve("贵州茅台")
    assert r is not None and r.code == "600519"


def test_resolve_fake_code_returns_none(master):
    """反幻觉关键：假代码返回 None，不抛异常。"""
    assert master.resolve("999999") is None
    assert master.resolve("000000") is None


def test_resolve_empty_and_garbage(master):
    assert master.resolve("") is None
    assert master.resolve("不是股票") is None


def test_resolve_historical_name_dual_source(master):
    """历史曾用名经 sz_change_name 双源交叉命中（反幻觉护栏核心）。
    深发展 → 平安银行(000001) 是经典改名；若该记录不在数据中则 skip（不判失败）。
    """
    r = master.resolve("深发展A") or master.resolve("深发展")
    if r is None:
        pytest.skip("曾用名'深发展'未在 sz_change_name 中命中")
    assert r.code == "000001"
    assert "深发展" in r.historical_names


# ---- is_real_at ----


def test_is_real_at_listed_after_event(master):
    """海光信息 688041 上市 2022-08-12 → 2020 年事件期不在市。"""
    assert master.is_real_at("688041", date(2020, 1, 1)) is False


def test_is_real_at_in_market(master):
    assert master.is_real_at("600519", date(2020, 1, 1)) is True


def test_is_real_at_fake_code(master):
    assert master.is_real_at("999999", date(2020, 1, 1)) is False


# ---- constituents（降级 hint）----


def test_constituents_best_effort(master):
    """行业成分股降级为深市所属行业过滤；可能为部分/空，仅作 hint。"""
    codes = master.constituents("银行")
    # 不强制非空（数据/行业名可能不含"银行"关键词），但必须是 list[str]
    assert isinstance(codes, list)
    assert all(isinstance(c, str) and len(c) == 6 for c in codes)


# ---- 离线（mock _fetch_df，不触网）：空主数据守卫 ----


def _sh_df(n: int, start: int = 600100) -> pd.DataFrame:
    """构造 n 只沪市标的（避开 600001/600002 留给退市 mock）。"""
    return pd.DataFrame({
        "证券代码": [str(start + i) for i in range(n)],
        "证券简称": [f"沪股{i}" for i in range(n)],
        "上市日期": ["1999-11-10"] * n,
    })


def test_all_sources_failure_raises_not_silent_empty(tmp_path, monkeypatch):
    """全部源失败 → ensure_loaded 抛 RuntimeError（不落盘、不置 _loaded）。"""
    def _fail(self, fn, *args):
        raise ConnectionError("网络不可达")

    monkeypatch.setattr(StockMaster, "_fetch_df", _fail)
    sm = StockMaster(cache_dir=tmp_path)
    with pytest.raises(RuntimeError):
        sm.ensure_loaded()
    assert sm._loaded is False
    assert list(tmp_path.glob("*.pkl")) == []  # 空库不落盘（防永久缓存）


def test_small_master_raises(tmp_path, monkeypatch):
    """主数据 < 1000 只（源部分失败/被截断）→ 抛错而非静默半库。"""
    def _fetch(self, fn, *args):
        if fn == "stock_info_sh_name_code":
            return _sh_df(10)
        raise ConnectionError("不可达")

    monkeypatch.setattr(StockMaster, "_fetch_df", _fetch)
    sm = StockMaster(cache_dir=tmp_path)
    with pytest.raises(RuntimeError):
        sm.ensure_loaded()
    assert sm._loaded is False
    assert list(tmp_path.glob("*.pkl")) == []


def test_empty_cache_treated_as_miss(tmp_path, monkeypatch):
    """缓存解出的 _master 为空 → 视为 miss（删缓存文件并重新拉取）。"""
    sm0 = StockMaster(cache_dir=tmp_path)
    sm0._cache_path().write_bytes(pickle.dumps(({}, {}, {}, {})))  # 篡改：空主数据缓存

    calls = {"n": 0}

    def _fetch(self, fn, *args):
        calls["n"] += 1
        if fn == "stock_info_sh_name_code":
            return _sh_df(1200)
        return pd.DataFrame()  # 其余源空（不影响主数据量）

    monkeypatch.setattr(StockMaster, "_fetch_df", _fetch)
    sm = StockMaster(cache_dir=tmp_path)
    sm.ensure_loaded()
    assert calls["n"] >= 1  # 触发了重新拉取
    assert len(sm._master) == 1200
    assert sm._loaded is True


# ---- 离线（mock）：退市标的幸存者偏差 ----


def _delist_fetch(fn, *args):
    if fn == "stock_info_sh_name_code":
        return _sh_df(1200)
    if fn == "stock_info_sh_delist":
        return pd.DataFrame({
            "公司代码": ["600001", "600002"],
            "公司简称": ["退市甲", "退市乙"],
            "上市日期": ["1995-01-01", "2000-06-01"],
            "暂停上市日期": ["2019-05-10", None],
        })
    return pd.DataFrame()


def test_delisted_stock_resolvable(tmp_path, monkeypatch):
    """退市标的并入可 resolve 索引（不再只活在 is_delisted 标记里）。"""
    monkeypatch.setattr(StockMaster, "_fetch_df", lambda self, fn, *a: _delist_fetch(fn, *a))
    sm = StockMaster(cache_dir=tmp_path)
    sm.ensure_loaded()
    r = sm.resolve("600001")
    assert r is not None
    assert r.name == "退市甲"
    assert r.is_delisted is True
    assert r.delist_date == date(2019, 5, 10)
    assert r.listing_date == date(1995, 1, 1)  # 退市接口自带上市日期
    assert r.market == "沪"
    # 退市日期缺失的也仍可 resolve（is_delisted=True, delist_date=None）
    r2 = sm.resolve("600002")
    assert r2 is not None and r2.is_delisted is True and r2.delist_date is None
    # 名称反查也命中退市标的
    assert sm.resolve("退市甲").code == "600001"


def test_delisted_is_real_at_before_and_after(tmp_path, monkeypatch):
    """退市标的在市判定：退市前在市、退市后不在市（无幸存者偏差）。"""
    monkeypatch.setattr(StockMaster, "_fetch_df", lambda self, fn, *a: _delist_fetch(fn, *a))
    sm = StockMaster(cache_dir=tmp_path)
    sm.ensure_loaded()
    assert sm.is_real_at("600001", date(2018, 1, 1)) is True  # 上市后、退市前
    assert sm.is_real_at("600001", date(2020, 1, 1)) is False  # 退市后
    assert sm.is_real_at("600001", date(1990, 1, 1)) is False  # 上市前


def test_sz_blank_code_row_skipped(tmp_path, monkeypatch):
    """深市行 A股代码为空串/非数字 → 跳过（不产生 '000000' 假代码）。"""
    def _fetch(self, fn, *args):
        if fn == "stock_info_sh_name_code":
            return _sh_df(1200)
        if fn == "stock_info_sz_name_code":
            return pd.DataFrame({
                "A股代码": ["", "000abc", None],
                "A股简称": ["空代码股", "非数字股", "缺失股"],
                "A股上市日期": ["2010-01-01"] * 3,
                "所属行业": ["银行"] * 3,
            })
        return pd.DataFrame()

    monkeypatch.setattr(StockMaster, "_fetch_df", _fetch)
    sm = StockMaster(cache_dir=tmp_path)
    sm.ensure_loaded()
    assert "000000" not in sm._master
    assert all(c.isdigit() for c in sm._master)
    assert sm.resolve("空代码股") is None
