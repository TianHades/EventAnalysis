"""HKStockMaster：港股标的主数据 + 反查（Phase 5）。

spike 确认：stock_hk_spot（~2700只，代码/中英文名）+ 东财 F10 懒加载。
列名对齐 akshare 1.18 源码（stock_profile_em.py）：
- 公司资料 stock_hk_company_profile_em：所属行业 / 公司介绍（ORG_PROFILE）。
  注意：该接口无上市日期列（也无"公司概况"列）。
- 证券资料 stock_hk_security_profile_em：上市日期（LISTING_DATE）→ listing_date 来源；
  该接口失败时 listing_date 置 None，在市判定退化为 resolve 校验（不判上市前）。
港股 spot 行业字段常空，行业/上市日懒加载 + 缓存。
"""

from __future__ import annotations

import hashlib
import pickle
from datetime import date as date_cls
from pathlib import Path

import pandas as pd
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ._net import network_retry_predicate
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
    """港股标的字典 + 反查。stock_hk_spot 加载 + F10 profile 懒加载。

    空主数据守卫：spot 失败/被截断（< _MIN_MASTER_SIZE）时 ensure_loaded 抛
    RuntimeError 且不落盘；resolve 捕获返回 None（永不抛契约）。
    """

    _MIN_MASTER_SIZE = 500  # 港股 ~2700 只；低于此视为源异常

    def __init__(self, cache_dir: str | Path = "cache/stockmaster_hk") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = False
        self._master: dict[str, dict] = {}  # code -> {name, name_en}
        self._name_index: dict[str, str] = {}  # 中文名/英文名大写 -> code
        self._profile_cache: dict[str, dict] = {}  # code -> profile（行业/上市日）

    def _cache_path(self) -> Path:
        return self.cache_dir / f"{hashlib.md5(b'hk-stockmaster-v2').hexdigest()}.pkl"

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = self._cache_path()
        if path.exists():
            try:
                with path.open("rb") as f:
                    data = pickle.load(f)
                if len(data[0]) >= self._MIN_MASTER_SIZE:  # 空/异常小缓存视为 miss
                    self._master, self._name_index = data
                    self._loaded = True
                    return
                path.unlink(missing_ok=True)  # 删除坏缓存，走重新拉取
            except Exception:
                path.unlink(missing_ok=True)
        self._fetch_and_build()  # spot 失败自然抛（重试 3 次后 reraise）
        if len(self._master) < self._MIN_MASTER_SIZE:
            raise RuntimeError(
                f"HKStockMaster 主数据仅 {len(self._master)} 条（阈值 "
                f"{self._MIN_MASTER_SIZE}），疑似数据源不可达——拒绝落盘空库"
            )
        try:
            with path.open("wb") as f:
                pickle.dump((self._master, self._name_index), f)
        except Exception:
            pass
        self._loaded = True

    @retry(
        retry=retry_if_exception(network_retry_predicate),
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
        retry=retry_if_exception(network_retry_predicate),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_profile(self, code: str) -> dict:
        import akshare as ak

        # 公司资料：行业 + 公司介绍（真实列；无上市日期列）
        df = ak.stock_hk_company_profile_em(symbol=code)
        industry: str | None = None
        summary: str | None = None
        if not df.empty:
            r = df.iloc[0]
            industry = str(r.get("所属行业") or "").strip() or None
            summary = str(r.get("公司介绍") or "").strip() or None
        # 证券资料：上市日期（东财 profile 无上市日，从此接口补）
        listing_date: date_cls | None = None
        try:
            sec = ak.stock_hk_security_profile_em(symbol=code)
            if not sec.empty:
                listing_date = self._parse_date(sec.iloc[0].get("上市日期"))
        except Exception:
            pass  # 单接口失败 → listing_date None，在市判定退化为 resolve 校验
        return {"industry": industry, "listing_date": listing_date, "summary": summary}

    @staticmethod
    def _parse_date(v) -> date_cls | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            return pd.to_datetime(v).date()
        except Exception:
            return None

    def _get_profile(self, code: str) -> dict:
        """懒加载 F10 profile + 内存缓存。"""
        if code in self._profile_cache:
            return self._profile_cache[code]
        try:
            p = self._fetch_profile(code)
        except Exception:
            p = {}
        self._profile_cache[code] = p
        return p

    def resolve(self, identifier: str) -> ResolvedStock | None:
        """反查：5位代码或中英文名 → ResolvedStock（港股）。永不抛异常。

        ensure_loaded 失败（首次加载网络断）→ 返回 None，不炸整条 replay。
        """
        try:
            self.ensure_loaded()
        except Exception:
            return None
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
        """事件期是否在市：真实标的 + 上市早于 on_date（港股无退市字段，MVP 不判退市）。

        listing_date 缺失（证券资料接口失败）时退化为仅 resolve 校验。
        """
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
