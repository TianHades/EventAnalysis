"""QuoteAdapter：行情数据适配器（spike 确认主源为 sina）。

spike 结论（见 docs/superpowers/specs/2026-06-30-spike-results.md）：
- 个股后复权日线用 `stock_zh_a_daily`（sina 源）；eastmoney 的 `stock_zh_a_hist` 不可达。
- 指数日线用 `stock_zh_index_daily`（价格指数；全收益 H00300.CSI 不可得）。
- 交易日历用 `tool_trade_date_hist_sina`。
- 市值从 `outstanding_share × close` 推导（spot 接口不可达）。

接口化（QuoteAdapter Protocol）便于后续切换数据源；带本地 pickle 缓存 + tenacity 重试。
"""

from __future__ import annotations

import hashlib
import pickle
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


def to_sina_symbol(code: str) -> str:
    """6 位 A 股代码 → sina 行情符号（sh/sz/bj 前缀）。

    6/9 开头→sh（含科创板 688、B股 900）；0/3 开头→sz；8/4/920 开头→bj（北交所）。
    """
    code = code.strip()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"非法 A 股代码: {code!r}")
    if code.startswith("920"):
        return f"bj{code}"  # 北交所新号段（920xxx），先于 B 股 9 段判定
    first = code[0]
    if first in ("6", "9"):
        return f"sh{code}"
    if first in ("0", "3"):
        return f"sz{code}"
    if first in ("8", "4"):
        return f"bj{code}"
    raise ValueError(f"无法识别板块的代码: {code!r}")


class QuoteAdapter(Protocol):
    """行情适配器接口。"""

    def daily_adjusted(
        self, code: str, start: date, end: date, adjust: str = "hfq"
    ) -> pd.DataFrame:
        """个股日线（后复权默认）。列：date/open/high/low/close/volume/amount/outstanding_share/turnover。"""
        ...

    def index_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """指数日线（sina 符号，如 sh000300）。列：date/open/high/low/close/volume。"""
        ...

    def trade_dates(self) -> list[date]:
        """A 股交易日历（升序）。"""
        ...


def _cache_key(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def _call_with_timeout(func, kwargs: dict, timeout: int = 60):
    """daemon 线程包装 akshare 调用，超时抛 TimeoutError（避免 sina 网络挂起卡死整个 replay）。"""
    import threading

    box: dict = {"result": None, "exc": None}

    def _run() -> None:
        try:
            box["result"] = func(**kwargs)
        except Exception as e:
            box["exc"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError(f"{getattr(func, '__name__', 'akshare')} 超时({timeout}s)")
    if box["exc"]:
        raise box["exc"]
    return box["result"]


class AkshareQuoteAdapter:
    """akshare + sina 实现。本地缓存按 (symbol, start, end, adjust) hash。"""

    def __init__(self, cache_dir: str | Path = "cache/quotes") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._trade_dates_cache: list[date] | None = None

    # ---- 缓存 ----

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.pkl"

    def _read_cache(self, key: str) -> pd.DataFrame | None:
        path = self._cache_path(key)
        if path.exists():
            try:
                return pd.read_pickle(path)
            except Exception:
                path.unlink(missing_ok=True)
        return None

    def _write_cache(self, key: str, df: pd.DataFrame) -> None:
        try:
            df.to_pickle(self._cache_path(key))
        except Exception:
            pass  # 缓存写失败不影响主流程

    def clear_cache(self) -> None:
        for p in self.cache_dir.glob("*.pkl"):
            p.unlink(missing_ok=True)

    # ---- 网络拉取（带重试） ----

    @retry(
        retry=retry_if_exception_type(ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_daily(self, sina_sym: str, start: date, end: date, adjust: str) -> pd.DataFrame:
        import akshare as ak

        df = _call_with_timeout(ak.stock_zh_a_daily, {
            "symbol": sina_sym,
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "adjust": adjust,
        })
        return self._normalize_daily(df)

    @retry(
        retry=retry_if_exception_type(ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_index(self, symbol: str) -> pd.DataFrame:
        import akshare as ak

        df = _call_with_timeout(ak.stock_zh_index_daily, {"symbol": symbol})
        if "date" not in df.columns:
            raise KeyError("date")
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    @staticmethod
    def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
        if "date" not in df.columns:
            raise KeyError("date")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    # ---- 公开接口 ----

    @staticmethod
    def _needs_refresh(df: pd.DataFrame | None, end: date) -> bool:
        """缓存是否缺近端数据：止于所需日期（end 与今天取小）前 3 个日历日以上。

        历史复盘的缓存天然覆盖；monitor/盘后校准处理近期事件时靠此触发刷新，
        否则基准止于首次缓存日，度量静默退化为 None。
        """
        if df is None or df.empty or "date" not in df.columns:
            return True
        needed = min(end, date.today())
        try:
            return max(df["date"]) < needed - timedelta(days=3)
        except Exception:
            return True

    def daily_adjusted(
        self, code: str, start: date, end: date, adjust: str = "hfq"
    ) -> pd.DataFrame:
        sina_sym = to_sina_symbol(code)
        key = _cache_key(sina_sym, adjust, start.isoformat(), end.isoformat())
        cached = self._read_cache(key)
        if cached is not None and not self._needs_refresh(cached, end):
            return cached
        try:
            df = self._fetch_daily(sina_sym, start, end, adjust)
        except Exception:
            if cached is not None:
                return cached  # 刷新失败退回旧缓存（好于无数据）
            raise
        self._write_cache(key, df)
        return df

    def index_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        # 指数全量拉取一次缓存，缺近端数据时自动刷新（指数缓存键不含起止日期）
        key = _cache_key("idx", symbol)
        cached = self._read_cache(key)
        if cached is not None and not self._needs_refresh(cached, end):
            df = cached
        else:
            try:
                df = self._fetch_index(symbol)
                self._write_cache(key, df)
            except Exception:
                if cached is None:
                    raise
                df = cached
        mask = (df["date"] >= start) & (df["date"] <= end)
        return df.loc[mask].reset_index(drop=True)

    def trade_dates(self) -> list[date]:
        if self._trade_dates_cache is not None:
            return self._trade_dates_cache
        key = _cache_key("trade_dates")
        cached = self._read_cache(key)
        if cached is not None:
            dates = sorted(cached["trade_date"].tolist())
            self._trade_dates_cache = dates
            return dates
        dates_df = self._fetch_trade_dates()
        self._write_cache(key, dates_df)
        result = sorted(dates_df["trade_date"].tolist())
        self._trade_dates_cache = result
        return result

    @retry(
        retry=retry_if_exception_type(ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_trade_dates(self) -> pd.DataFrame:
        import akshare as ak

        df = _call_with_timeout(ak.tool_trade_date_hist_sina, {})
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df
