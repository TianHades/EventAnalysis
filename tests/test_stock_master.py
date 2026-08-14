"""StockMaster 测试：resolve（代码/名称/曾用名/假代码）+ is_real_at + constituents（真实数据，网络失败 skip）。"""

from datetime import date

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
    except ConnectionError as e:
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
