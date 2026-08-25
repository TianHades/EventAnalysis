"""HKQuoteAdapter：港股行情适配器（Phase 5）。

spike 确认（2026-07-04）：sina 源可用
- 港股日线：stock_hk_daily(symbol, adjust='hfq')，5位代码，列 date/open/high/low/close/volume
  注：不支持 start_date/end_date，拉全量后按日期过滤 + 缓存
- 港股指数：stock_hk_index_daily_sina(symbol)，HSI 恒生 / HSTECH 恒生科技
- 交易日历：tool_trade_date_hist_sina（与 A 股共用）

接口同 A 股 QuoteAdapter（daily_adjusted / index_daily / trade_dates）；带本地 pickle 缓存 + tenacity 重试。
"""

from __future__ import annotations

import hashlib
import pickle
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ._net import call_with_timeout, network_retry_predicate


def to_hk_symbol(code: str) -> str:
    """港股代码标准化为 5 位（如 00700）。stock_hk_daily 用 5 位代码。"""
    code = code.strip()
    if code.lower().startswith("hk"):
        code = code[2:]
    if not code.isdigit():
        raise ValueError(f"非法港股代码: {code!r}")
    if len(code) > 5:
        raise ValueError(f"非法港股代码（超 5 位，疑似 A 股代码误传）: {code!r}")
    return code.zfill(5)


def _cache_key(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


class HKQuoteAdapter:
    """港股行情：stock_hk_daily（sina，全量缓存+过滤）+ stock_hk_index_daily_sina + 共用交易日历。"""

    def __init__(self, cache_dir: str | Path = "cache/quotes_hk") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._trade_dates_cache: list[date] | None = None

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
            pass

    @retry(
        retry=retry_if_exception(network_retry_predicate),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_daily(self, symbol: str, adjust: str) -> pd.DataFrame:
        import akshare as ak

        df = call_with_timeout(ak.stock_hk_daily, {"symbol": symbol, "adjust": adjust})
        return self._normalize_daily(df)

    @retry(
        retry=retry_if_exception(network_retry_predicate),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_index(self, symbol: str) -> pd.DataFrame:
        import akshare as ak

        df = call_with_timeout(ak.stock_hk_index_daily_sina, {"symbol": symbol})
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

    @staticmethod
    def _needs_refresh(df: pd.DataFrame | None, end: date) -> bool:
        """缓存是否缺近端数据（与 A 股 QuoteAdapter 同口径：止于所需日期前 3 日历日以上）。"""
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
        """港股日线（后复权默认，全量缓存+按日期过滤）。列：date/open/high/low/close/volume。"""
        symbol = to_hk_symbol(code)
        key = _cache_key("hk", symbol, adjust)
        cached = self._read_cache(key)
        if cached is not None and not self._needs_refresh(cached, end):
            df = cached
        else:
            try:
                df = self._fetch_daily(symbol, adjust)
                self._write_cache(key, df)
            except Exception:
                if cached is None:
                    raise
                df = cached
        mask = (df["date"] >= start) & (df["date"] <= end)
        return df.loc[mask].reset_index(drop=True)

    def index_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """港股指数日线（sina 符号，HSI / HSTECH）。列：date/open/high/low/close/volume。"""
        key = _cache_key("hk_idx", symbol)
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
        """交易日历（与 A 股共用 sina）。"""
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
        retry=retry_if_exception(network_retry_predicate),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_trade_dates(self) -> pd.DataFrame:
        import akshare as ak

        df = call_with_timeout(ak.tool_trade_date_hist_sina, {})
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df
