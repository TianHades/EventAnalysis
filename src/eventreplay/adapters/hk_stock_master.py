"""HKStockMaster：港股标的主数据 + 反查（Phase 5）。

spike 确认：stock_hk_spot（2787只，代码/中英文名）+ stock_hk_company_profile_em（公司资料/行业/上市日，懒加载）。
港股 spot 行业字段常空，行业/上市日从 company_profile_em 懒加载 + 缓存。
"""

from __future__ import annotations

import hashlib
import pickle
from datetime import date as date_cls
from pathlib import Path

import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .stock_master import ResolvedStock, clean_name


def infer_hk_board(code: str) -> str:
    """港股代码 → 板块（MVP 都港股主板；GEM 识别略）。"""
    return "港股主板"


def _is_hk_code_like(s: str) -> bool:
    s = s.strip()
    if s.lower().startswith("hk"):
        s = s[2:]
    return s.isdigit() and 1 <= len(s) <= 5


def _normalize_hk_code(s: str) -> str:
    s = s.strip()
    if s.lower().startswith("hk"):
        s = s[2:]
    return s.zfill(5)


class HKStockMaster:
    """港股标的字典 + 反查。stock_hk_spot 加载 + company_profile_em 懒加载。"""

    def __init__(self, cache_dir: str | Path = "cache/stockmaster_hk") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = False
        self._master: dict[str, dict] = {}  # code -> {name, name_en}
        self._name_index: dict[str, str] = {}  # 中文名/英文名大写 -> code
        self._profile_cache: dict[str, dict] = {}  # code -> profile（行业/上市日）

    def _cache_path(self) -> Path:
        return self.cache_dir / f"{hashlib.md5(b'hk-stockmaster-v1').hexdigest()}.pkl"

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = self._cache_path()
        if path.exists():
            try:
                with path.open("rb") as f:
                    self._master, self._name_index = pickle.load(f)
                self._loaded = True
                return
            except Exception:
                path.unlink(missing_ok=True)
        self._fetch_and_build()
        try:
            with path.open("wb") as f:
                pickle.dump((self._master, self._name_index), f)
        except Exception:
            pass
        self._loaded = True

    @retry(
        retry=retry_if_exception_type(ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_spot(self) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_hk_spot()

    def _fetch_and_build(self) -> None:
        df = self._fetch_spot()
        for _, r in df.iterrows():
            code = str(r.get("代码") or "").zfill(5)
            if not code or not code.isdigit():
                continue
            name = str(r.get("中文名称") or "").strip()
            name_en = str(r.get("英文名称") or "").strip()
            self._master[code] = {"name": name, "name_en": name_en}
            if name:
                self._name_index.setdefault(name, code)
            if name_en:
                self._name_index.setdefault(name_en.upper(), code)

    @retry(
        retry=retry_if_exception_type(ConnectionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_profile(self, code: str) -> dict:
        import akshare as ak

        df = ak.stock_hk_company_profile_em(symbol=code)
        if df.empty:
            return {}
        r = df.iloc[0]
        return {
            "industry": str(r.get("所属行业") or "").strip() or None,
            "listing_date": self._parse_date(r.get("公司上市日期")),
            "summary": str(r.get("公司概况") or "").strip(),
        }

    @staticmethod
    def _parse_date(v) -> date_cls | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            return pd.to_datetime(v).date()
        except Exception:
            return None

    def _get_profile(self, code: str) -> dict:
        """懒加载 company_profile_em + 内存缓存。"""
        if code in self._profile_cache:
            return self._profile_cache[code]
        try:
            p = self._fetch_profile(code)
        except Exception:
            p = {}
        self._profile_cache[code] = p
        return p

    def resolve(self, identifier: str) -> ResolvedStock | None:
        """反查：5位代码或中英文名 → ResolvedStock（港股）。永不抛异常。"""
        self.ensure_loaded()
        ident = identifier.strip()
        if not ident:
            return None
        code: str | None = None
        if _is_hk_code_like(ident):
            code = _normalize_hk_code(ident)
        else:
            code = self._name_index.get(ident) or self._name_index.get(ident.upper())
            if code is None:
                cleaned = clean_name(ident)
                if cleaned and cleaned != ident:
                    code = self._name_index.get(cleaned) or self._name_index.get(cleaned.upper())
        if code is None or code not in self._master:
            return None
        info = self._master[code]
        profile = self._get_profile(code)
        return ResolvedStock(
            code=code,
            name=info["name"],
            market="港股",
            board=infer_hk_board(code),
            listing_date=profile.get("listing_date"),
            industry=profile.get("industry"),
        )

    def is_real_at(self, code: str, on_date: date_cls) -> bool:
        """事件期是否在市：真实标的 + 上市早于 on_date（港股无退市字段，MVP 不判退市）。"""
        r = self.resolve(code)
        if r is None:
            return False
        if r.listing_date is not None and r.listing_date >= on_date:
            return False
        return True

    def constituents(self, industry_keyword: str) -> list[str]:
        """按关键词过滤（港股 spot 无行业，MVP 按名称关键词；行业过滤需 profile 逐只加载太慢）。"""
        self.ensure_loaded()
        kw = industry_keyword.strip()
        if not kw:
            return []
        return [code for code, info in self._master.items()
                if info.get("name") and kw in info["name"]]
