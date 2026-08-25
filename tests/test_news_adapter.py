"""AkshareNewsAdapter 测试：mock akshare → NewsItem[] + 真实 smoke。

另覆盖 CCTVNewsAdapter / CLSNewsAdapter / Jin10NewsAdapter 的缺陷修复
（时间戳解析 / 单条坏数据隔离 / 真实列名 + id 全文 hash）。
"""

from datetime import date, datetime, time as dtime

import pandas as pd
import pytest

from eventreplay.adapters.news_adapter import (
    AkshareNewsAdapter,
    CLSNewsAdapter,
    CCTVNewsAdapter,
    JiuyanNewsAdapter,
    Jin10NewsAdapter,
)


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


# ---- CCTVNewsAdapter：date 列 "YYYYMMDD" 原始串解析 ----


def test_cctv_fetch_by_date_published_at_parsed(monkeypatch):
    """date 列是 'YYYYMMDD' 原始字符串 → strptime 解析（传 str 给 pydantic 会按 unix 秒解析成 1970）。"""
    import akshare as ak

    df = pd.DataFrame({
        "date": ["20240701"],
        "title": ["国务院印发新政"],
        "content": ["全文略"],
    })
    monkeypatch.setattr(ak, "news_cctv", lambda date: df)
    items = CCTVNewsAdapter().fetch_by_date(date(2024, 7, 1))
    assert len(items) == 1
    assert items[0].published_at == datetime(2024, 7, 1)


def test_cctv_network_failure_returns_empty(monkeypatch):
    import akshare as ak

    monkeypatch.setattr(ak, "news_cctv", lambda date: (_ for _ in ()).throw(RuntimeError("网络")))
    assert CCTVNewsAdapter().fetch_by_date(date(2024, 1, 1)) == []


# ---- CLSNewsAdapter：真实列名 + id 全文 hash + 坏行隔离 ----


def _cls_df():
    """akshare stock_info_global_cls 真实列（stock_info.py）：标题/内容/发布日期/发布时间。"""
    return pd.DataFrame({
        "标题": ["央行开展1000亿逆回购操作", "央行开展1000亿逆回购操作"],
        "内容": ["净投放500亿，利率持平", "净投放300亿，缩量续作"],
        "发布日期": [date(2024, 7, 1), date(2024, 7, 1)],
        "发布时间": [dtime(9, 15), dtime(9, 16)],
    })


def test_cls_real_columns_and_distinct_ids(monkeypatch):
    """同 title 前缀的模板化快讯：id 用 title+content 全文 hash，互不覆盖。"""
    import akshare as ak

    monkeypatch.setattr(ak, "stock_info_global_cls", lambda symbol="全部": _cls_df())
    items = CLSNewsAdapter().fetch_latest()
    assert len(items) == 2
    assert items[0].content == "净投放500亿，利率持平"
    assert items[0].published_at == datetime(2024, 7, 1, 9, 15)  # 发布日期+发布时间拼接
    assert items[1].published_at == datetime(2024, 7, 1, 9, 16)
    assert items[0].id != items[1].id


def test_cls_missing_content_skipped_no_title_echo(monkeypatch):
    """内容缺失 → 跳过该条（不复读 title 捏造内容）。"""
    import akshare as ak

    df = pd.DataFrame({
        "标题": ["只有标题的快讯"],
        "内容": [None],
        "发布日期": [date(2024, 7, 1)],
        "发布时间": [dtime(10, 0)],
    })
    monkeypatch.setattr(ak, "stock_info_global_cls", lambda symbol="全部": df)
    assert CLSNewsAdapter().fetch_latest() == []


def test_cls_missing_named_columns_skipped_not_positional(monkeypatch):
    """返回列缺失标准名 → 跳过（不按 iloc 位置兜底取到发布日期当 content）。"""
    import akshare as ak

    df = pd.DataFrame({"col_a": ["x"], "col_b": ["y"]})
    monkeypatch.setattr(ak, "stock_info_global_cls", lambda symbol="全部": df)
    assert CLSNewsAdapter().fetch_latest() == []


def test_cls_network_failure_returns_empty(monkeypatch):
    import akshare as ak

    def _raise(symbol="全部"):
        raise RuntimeError("网络")

    monkeypatch.setattr(ak, "stock_info_global_cls", _raise)
    assert CLSNewsAdapter().fetch_latest() == []


def test_cls_fetch_by_date_returns_empty():
    """财联社快讯无按日查询能力 → 返回空（而非语义错误的今日快讯）。"""
    assert CLSNewsAdapter().fetch_by_date(date(2024, 1, 1)) == []


# ---- Jin10NewsAdapter：单条坏数据隔离 + time 解析 ----


class _Jin10Resp:
    @staticmethod
    def json():
        return {"data": [
            {"id": 1, "time": "1720000000", "data": {"content": "美联储加息25个基点"}},
            {"id": 2, "time": "bad-time", "data": {"content": "坏时间戳条目"}},
            {"id": 3, "time": "", "data": {"content": "空时间条目"}},
            {"id": 4, "data": {"content": "无时间字段条目"}},
            {"id": 5, "time": "1720000001"},  # 无 content
            {"id": 6, "time": "1720000002", "data": {"content": ""}},  # 空 content
            {"id": 7, "time": "1720000003", "data": None},  # data 非 dict
        ]}


def test_jin10_bad_items_skipped_good_kept(monkeypatch):
    """单条坏数据（非法 time / 缺字段 / 无 content）隔离：坏条目跳过，好条目保留。"""
    import requests as _req

    monkeypatch.setattr(_req, "get", lambda *a, **k: _Jin10Resp())
    items = Jin10NewsAdapter().fetch_latest()
    assert len(items) == 4  # id 1/2/3/4 保留（time 坏只降级为 None）；id 5/6/7 跳过（无 content）
    assert items[0].published_at == datetime.fromtimestamp(1720000000)
    assert items[1].published_at is None  # 坏时间戳 → None
    assert items[2].published_at is None  # 空时间 → None
    assert items[3].published_at is None  # 无时间字段 → None
    assert all(i.source == "金十数据" for i in items)


def test_jin10_fetch_by_date_returns_empty():
    """金十 flash list 仅实时，无按日查询能力 → 返回空（而非语义错误的今日快讯）。"""
    assert Jin10NewsAdapter().fetch_by_date(date(2024, 1, 1)) == []


def test_jin10_network_failure_returns_empty(monkeypatch):
    import requests as _req

    def _raise(*a, **k):
        raise RuntimeError("网络")

    monkeypatch.setattr(_req, "get", _raise)
    assert Jin10NewsAdapter().fetch_latest() == []
