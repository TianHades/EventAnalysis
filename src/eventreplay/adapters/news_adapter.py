"""NewsAdapter：财经新闻采集（Phase 4 功能1/2 共享源）。

spike 确认（2026-07-04）：
- stock_news_main_cx：财新主流财经新闻，100 条实时，tag/summary/url（实时主源）
- news_cctv(date)：央视新闻联播按日查，date/title/content（按日源）
- 韭研公社投研帖：见 JiuyanNewsAdapter（SSR + HTML 解析）
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Protocol

import pandas as pd

from ..schema.news import NewsItem


class NewsAdapter(Protocol):
    """新闻采集接口。"""

    def fetch_latest(self) -> list[NewsItem]: ...
    def fetch_by_date(self, d: date) -> list[NewsItem]: ...


def _row_id(*parts: str) -> str:
    return hashlib.md5("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:16]


class AkshareNewsAdapter:
    """akshare 新闻源：stock_news_main_cx（实时）+ news_cctv(date)（按日）。"""

    def fetch_latest(self) -> list[NewsItem]:
        import akshare as ak

        try:
            df = ak.stock_news_main_cx()
        except Exception:
            return []
        out: list[NewsItem] = []
        for _, r in df.iterrows():
            summary = str(r.get("summary") or "")
            url = str(r.get("url") or "") or None
            out.append(NewsItem(
                id=_row_id(summary, url or ""),
                title=summary[:80],
                content=summary,
                source="stock_news_main_cx",
                url=url,
            ))
        return out

    def fetch_by_date(self, d: date) -> list[NewsItem]:
        import akshare as ak

        try:
            df = ak.news_cctv(date=d.strftime("%Y%m%d"))
        except Exception:
            return []
        out: list[NewsItem] = []
        for _, r in df.iterrows():
            title = str(r.get("title") or "")
            content = str(r.get("content") or "")
            pub: datetime | None = None
            try:
                pub = datetime.strptime(str(r.get("date") or d.strftime("%Y%m%d")), "%Y%m%d")
            except ValueError:
                pub = None
            out.append(NewsItem(
                id=_row_id(title, d.isoformat()),
                title=title,
                content=content,
                published_at=pub,
                source="news_cctv",
            ))
        return out


class JiuyanNewsAdapter:
    """韭研公社投研帖（SSR，requests + BeautifulSoup 解析）。

    spike 2026-07-04：首页投研帖在 <section> 卡片，含
    .book-title(标题) / .html-text(摘要) / .fs13-ash(时间) / .source(关联个股)。
    事件日历 API（app.jiuyangongshe.com/.../timeline/news）需鉴权，MVP 不接入。
    合规：遵守 robots.txt + 服务条款，仅研究用途。
    """

    BASE = "https://www.jiuyangongshe.com/"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Referer": "https://www.jiuyangongshe.com/",
    }

    def fetch_latest(self) -> list[NewsItem]:
        import requests
        from bs4 import BeautifulSoup

        try:
            r = requests.get(self.BASE, headers=self.HEADERS, timeout=15)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            return []
        out: list[NewsItem] = []
        for section in soup.find_all("section"):
            title_el = section.select_one(".book-title")
            if not title_el:
                continue
            title = title_el.get_text().strip()
            content_el = section.select_one(".html-text") or section.select_one(".drvi")
            content = content_el.get_text().strip() if content_el else ""
            time_el = section.select_one(".fs13-ash")
            pub = self._parse_time(time_el.get_text().strip()) if time_el else None
            stocks = [s.get_text().strip() for s in section.select(".source")]
            full = content + (f" | 关联标的: {' '.join(stocks)}" if stocks else "")
            if not title:
                continue
            if not content and not pub:
                continue  # 无摘要且无时间（置顶/官方噪音）跳过
            out.append(NewsItem(
                id=_row_id(title, str(pub or "")),
                title=title[:80],
                content=full,
                published_at=pub,
                source="jiuyan",
            ))
        return out

    def fetch_by_date(self, d: date) -> list[NewsItem]:
        return []  # 韭研公社无公开按日查（事件日历 API 需鉴权）

    @staticmethod
    def _parse_time(s: str) -> datetime | None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None


class CCTVNewsAdapter:
    """央视新闻联播（akshare news_cctv，按日）。

    权威政策事件首发（国务院/部委重大政策）。每日 ~11 条。
    """

    def fetch_latest(self) -> list[NewsItem]:
        return self.fetch_by_date(date.today())

    def fetch_by_date(self, d: date) -> list[NewsItem]:
        import akshare as ak

        try:
            df = ak.news_cctv(date=d.strftime("%Y%m%d"))
        except Exception:
            return []
        out: list[NewsItem] = []
        for _, r in df.iterrows():
            title = str(r.get("title", ""))
            # date 列是 "YYYYMMDD" 原始字符串——直接传 str 给 pydantic 会按 unix 秒
            # 解析成 1970，必须 strptime（同 AkshareNewsAdapter.fetch_by_date）
            pub: datetime | None = None
            try:
                pub = datetime.strptime(str(r.get("date") or d.strftime("%Y%m%d")), "%Y%m%d")
            except ValueError:
                pub = None
            out.append(NewsItem(
                id=_row_id("cctv", str(r.get("date", "")), title),
                title=title,
                content=str(r.get("content", "")),
                source="央视新闻联播",
                published_at=pub,
            ))
        return out


class CLSNewsAdapter:
    """财联社全球（akshare stock_info_global_cls）。

    A 股实时快讯。akshare 封装，带异常降级。
    真实列（akshare stock_info.py）：标题/内容/发布日期/发布时间。
    """

    def fetch_latest(self) -> list[NewsItem]:
        import akshare as ak

        try:
            df = ak.stock_info_global_cls(symbol="全部")
        except Exception:
            return []
        out: list[NewsItem] = []
        for _, r in df.iterrows():
            title = str(r.get("标题") or r.get("title") or "").strip()
            content = str(r.get("内容") or r.get("content") or "").strip()
            if not title or not content:
                continue  # 列名缺失/内容缺失：跳过（不按位置兜底取错列、不复读 title 捏造内容）
            out.append(NewsItem(
                # title+content 全文 hash：模板化快讯 title 前缀相同也不互相覆盖
                id=hashlib.sha1(f"{title}|{content}".encode("utf-8")).hexdigest()[:16],
                title=title,
                content=content,
                published_at=self._parse_pub(r),
                source="财联社",
            ))
        return out

    def fetch_by_date(self, d: date) -> list[NewsItem]:
        return []  # 财联社快讯仅实时接口，无按日查询能力（返回今日快讯语义错误）

    @staticmethod
    def _parse_pub(r) -> datetime | None:
        """发布日期 + 发布时间 两列拼 published_at（缺任一列返回 None）。"""
        d, t = r.get("发布日期"), r.get("发布时间")
        if d is None or t is None:
            return None
        try:
            return datetime.combine(pd.Timestamp(d).date(), pd.Timestamp(str(t)).time())
        except Exception:
            return None


class Jin10NewsAdapter:
    """金十数据 flash list（requests API）。

    全球宏观+地缘快讯（快于国内媒体）。~21 条实时。
    """

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-app-id": "bVBF4FyRTn5NJF5n",
        "x-version": "1.0.0",
        "Origin": "https://www.jin10.com",
        "Referer": "https://www.jin10.com/",
    }

    def fetch_latest(self) -> list[NewsItem]:
        import requests

        try:
            r = requests.get(
                "https://flash-api.jin10.com/get_flash_list",
                headers=self._HEADERS,
                params={"max_time": "", "channel": "-8200"},
                timeout=10,
            )
            data = r.json().get("data", [])
        except Exception:
            return []
        out: list[NewsItem] = []
        for item in data:
            try:  # 单条坏数据跳过，不炸整源
                raw = item.get("data") or {}
                content = str(raw.get("content") or "") if isinstance(raw, dict) else ""
                if not content:
                    continue
                out.append(NewsItem(
                    id=_row_id("jin10", str(item.get("id", ""))),
                    title=content[:60],
                    content=content,
                    source="金十数据",
                    published_at=self._parse_time(item.get("time")),
                ))
            except Exception:
                continue
        return out

    def fetch_by_date(self, d: date) -> list[NewsItem]:
        return []  # 金十 flash list 仅实时，无按日查询能力（返回今日快讯语义错误）

    @staticmethod
    def _parse_time(v) -> datetime | None:
        """time 字段：可转 int 的 unix 秒字符串 → naive datetime（与其他源一致）；缺失/非法 None。"""
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            return datetime.fromtimestamp(int(s))
        except (ValueError, OSError, OverflowError):
            return None


class MultiSourceNewsAdapter:
    """多源融合：同时采集多个源 → 去重 → 合并返回。

    用法：MultiSourceNewsAdapter([AkshareNewsAdapter(), Jin10NewsAdapter(), CCTVNewsAdapter()])
    单源失败不影响其他源（各自 try-except 降级）。
    """

    def __init__(self, sources: list, max_per_source: int = 30) -> None:
        self.sources = sources
        self.max_per_source = max_per_source

    def _merge_dedup(self, batches: list[list[NewsItem]]) -> list[NewsItem]:
        """多源合并 + 按 title 去重（保留首个出现的）。"""
        seen: set[str] = set()
        out: list[NewsItem] = []
        for batch in batches:
            for item in batch:
                # 标题标准化去重（去空白/前缀，取前 40 字做指纹）
                import re
                norm = re.sub(r"[\s　]+", "", item.title)[:40]
                if norm in seen:
                    continue
                seen.add(norm)
                out.append(item)
        return out

    def fetch_latest(self) -> list[NewsItem]:
        batches: list[list[NewsItem]] = []
        for src in self.sources:
            try:
                items = src.fetch_latest()
                batches.append(items[: self.max_per_source])
            except Exception:
                pass  # 单源失败不影响其他
        return self._merge_dedup(batches)

    def fetch_by_date(self, d: date) -> list[NewsItem]:
        batches: list[list[NewsItem]] = []
        for src in self.sources:
            try:
                items = src.fetch_by_date(d)
                batches.append(items[: self.max_per_source])
            except Exception:
                pass
        return self._merge_dedup(batches)
