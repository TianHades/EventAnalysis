"""AkshareNewsAdapter 测试：mock akshare → NewsItem[] + 真实 smoke。"""

from datetime import date

import pandas as pd
import pytest

from eventreplay.adapters.news_adapter import AkshareNewsAdapter, JiuyanNewsAdapter


def test_fetch_latest(monkeypatch):
    df = pd.DataFrame({
        "tag": ["题材行业"],
        "summary": ["芯片管制升级倒逼国产替代"],
        "url": ["https://database.caixin.com/2026/example"],
    })
    import akshare as ak

    monkeypatch.setattr(ak, "stock_news_main_cx", lambda: df)
    items = AkshareNewsAdapter().fetch_latest()
    assert len(items) == 1
    assert "芯片管制" in items[0].title
    assert items[0].source == "stock_news_main_cx"
    assert items[0].url == "https://database.caixin.com/2026/example"
    assert items[0].id  # 非空


def test_fetch_by_date(monkeypatch):
    df = pd.DataFrame({
        "date": ["20240701"],
        "title": ["美联储维持利率不变"],
        "content": ["美联储宣布维持联邦基金利率..."],
    })
    import akshare as ak

    monkeypatch.setattr(ak, "news_cctv", lambda date: df)
    items = AkshareNewsAdapter().fetch_by_date(date(2024, 7, 1))
    assert len(items) == 1
    assert items[0].title == "美联储维持利率不变"
    assert items[0].source == "news_cctv"
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2024
    assert items[0].published_at.month == 7


def test_fetch_latest_empty(monkeypatch):
    """空 DataFrame → 空列表（不抛异常）。"""
    import akshare as ak

    monkeypatch.setattr(ak, "stock_news_main_cx", lambda: pd.DataFrame(columns=["summary", "url"]))
    assert AkshareNewsAdapter().fetch_latest() == []


@pytest.mark.skip(reason="真实网络 smoke，手动跑")
def test_real_smoke_fetch_latest():
    """真实 stock_news_main_cx 可达性。"""
    items = AkshareNewsAdapter().fetch_latest()
    assert len(items) > 0
    print(f"[smoke] fetch_latest: {len(items)} 条")


@pytest.mark.skip(reason="真实网络 smoke，手动跑")
def test_real_smoke_fetch_by_date():
    """真实 news_cctv 可达性。"""
    items = AkshareNewsAdapter().fetch_by_date(date(2024, 7, 1))
    assert len(items) > 0
    print(f"[smoke] fetch_by_date: {len(items)} 条")


# ---- JiuyanNewsAdapter ----

JIUYAN_HTML = """
<section>
  <div class="fs16-bold">白股精</div>
  <div class="fs13-ash">2026-07-03 22:28:11</div>
  <div class="book-title">创新药：1357亿美元出海</div>
  <div class="html-text">中国创新药反向收割全球药企</div>
  <div class="source-box"><div class="source">S 恒瑞医药</div><div class="source">S 百利天恒</div></div>
</section>
<section>
  <div class="book-title">无时间帖</div>
  <div class="html-text">有摘要但无时间</div>
</section>
"""


class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.encoding = "utf-8"


def test_jiuyan_fetch_latest(monkeypatch):
    import requests as _req

    monkeypatch.setattr(_req, "get", lambda *a, **k: _FakeResp(JIUYAN_HTML))
    items = JiuyanNewsAdapter().fetch_latest()
    assert len(items) == 2  # 两个 section
    assert "创新药" in items[0].title
    assert items[0].source == "jiuyan"
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2026
    assert "恒瑞医药" in items[0].content  # 关联标的进 content
    # 第二个无时间无摘要
    assert items[1].published_at is None


def test_jiuyan_fetch_by_date_empty():
    """韭研无公开按日查，返回空。"""
    assert JiuyanNewsAdapter().fetch_by_date(date(2024, 1, 1)) == []


@pytest.mark.skip(reason="真实网络 smoke，手动跑")
def test_real_smoke_jiuyan():
    """真实韭研公社投研帖抓取。"""
    items = JiuyanNewsAdapter().fetch_latest()
    assert len(items) > 0
    print(f"[smoke] jiuyan: {len(items)} 条, 首条: {items[0].title[:30]}")


def test_akshare_network_failure_returns_empty(monkeypatch):
    """akshare 调用失败 → 返回空（不抛异常，spec 单源失败降级）。"""
    import akshare as ak

    def _raise():
        raise RuntimeError("网络")

    monkeypatch.setattr(ak, "stock_news_main_cx", _raise)
    assert AkshareNewsAdapter().fetch_latest() == []

    def _raise_cctv(date):
        raise RuntimeError("网络")

    monkeypatch.setattr(ak, "news_cctv", _raise_cctv)
    assert AkshareNewsAdapter().fetch_by_date(date(2024, 1, 1)) == []


def test_jiuyan_network_failure_returns_empty(monkeypatch):
    """requests 失败 → 返回空。"""
    import requests as _req

    def _raise(*a, **k):
        raise RuntimeError("网络")

    monkeypatch.setattr(_req, "get", _raise)
    assert JiuyanNewsAdapter().fetch_latest() == []
