"""StockMaster：A 股标的主数据 + 真实性反查。

spike 结论（见 spike-results §6）：行业成分股（index_component_sw）不可得；
真实标的主数据用交易所 `stock_info_sh_name_code` + `stock_info_sz_name_code`（可达）。

核心职责：
- resolve(identifier)：反幻觉校验——给定代码或名称（含历史曾用名），返回真实标的或 None（永不抛异常）。
- is_real_at(code, on_date)：结合上市日/退市判定事件期是否在市。
- constituents(industry)：行业成分股——降级为"深市所属行业关键词过滤"（部分、仅 hint）。
"""

from __future__ import annotations

import hashlib
import pickle
import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path

import pandas as pd
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ._net import network_retry_predicate


@dataclass(frozen=True)
class ResolvedStock:
    code: str
    name: str
    market: str
    board: str | None
    listing_date: date_cls | None
    industry: str | None = field(default=None)  # 交易所所属行业（仅深市，证监会分类，非申万）
    # 局限：is_st/is_delisted 均为"当前状态"快照，无历史时点数据源——历史事件期的
    # ST/退市状态判定按 delist_date 时点推，ST 帽摘戴史不可得（不修）
    is_st: bool = False
    is_delisted: bool = False
    delist_date: date_cls | None = None
    historical_names: tuple[str, ...] = ()


def infer_board(code: str) -> str | None:
    """6 位代码 → 板块（与涨跌停规则对齐，spec §4.4）。"""
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith("688") or code.startswith("787"):
        return "科创板"
    if code.startswith("300") or code.startswith("301"):
        return "创业板"
    if code.startswith(("8", "4", "920")):
        return "北交所"
    if code[0] in ("6", "0"):
        return "主板"
    return None


def _is_code_like(s: str) -> bool:
    s = s.strip()
    if s.isdigit() and len(s) == 6:
        return True
    # 带 sh/sz/bj 前缀
    return s[:2] in ("sh", "sz", "bj") and s[2:].isdigit() and len(s[2:]) == 6


def _normalize_code(s: str) -> str:
    s = s.strip()
    return s[2:] if s[:2] in ("sh", "sz", "bj") else s


def clean_name(s: str) -> str:
    """去括号附注与空白（LLM 常输出 '宁德时代（300750）' 这类带后缀名称）。"""
    return re.sub(r"[（(][^（()）]*[)）]", "", s).strip()


class StockMaster:
    """标的字典 + 反查。加载后 pickle 缓存（~4600 标的，小）。

    空主数据守卫：全源失败/数据被截断（< _MIN_MASTER_SIZE）时 ensure_loaded 抛
    RuntimeError 且不落盘——防止把空库永久缓存导致后续进程静默反查全 None。
    """

    _MIN_MASTER_SIZE = 1000  # A 股 ~5300 只（含北交所）；低于此视为源异常

    def __init__(self, cache_dir: str | Path = "cache/stockmaster") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = False
        # code -> dict(name, listing_date, market, industry)（在市 + 退市并入）
        self._master: dict[str, dict] = {}
        # name -> code（当前名 + 历史曾用名）
        self._name_index: dict[str, str] = {}
        # code -> list[曾用名]
        self._historical: dict[str, list[str]] = {}
        # code -> dict(name, delist_date)
        self._delisted: dict[str, dict] = {}

    # ---- 加载 ----

    def _cache_path(self) -> Path:
        key = hashlib.md5(b"stockmaster-v3").hexdigest()
        return self.cache_dir / f"{key}.pkl"

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = self._cache_path()
        if path.exists():
            try:
                with path.open("rb") as f:
                    data = pickle.load(f)
                if len(data[0]) >= self._MIN_MASTER_SIZE:  # 空/异常小缓存视为 miss
                    self._master, self._name_index, self._historical, self._delisted = data
                    self._loaded = True
                    return
                path.unlink(missing_ok=True)  # 删除坏缓存，走重新拉取
            except Exception:
                path.unlink(missing_ok=True)
        self._fetch_and_build()
        if len(self._master) < self._MIN_MASTER_SIZE:
            raise RuntimeError(
                f"StockMaster 主数据仅 {len(self._master)} 条（阈值 "
                f"{self._MIN_MASTER_SIZE}），疑似数据源全部不可达——拒绝落盘空库"
            )
        try:
            with path.open("wb") as f:
                pickle.dump(
                    (self._master, self._name_index, self._historical, self._delisted), f
                )
        except Exception:
            pass
        self._loaded = True

    @retry(
        retry=retry_if_exception(network_retry_predicate),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_df(self, fn_name: str, *args) -> pd.DataFrame:
        import akshare as ak

        return getattr(ak, fn_name)(*args)

    def _fetch_and_build(self) -> None:
        # 沪市（主板 + 科创板；科创板需显式传参，否则只返回主板）
        for sh_arg in (None, "科创板"):
            try:
                sh = self._fetch_df("stock_info_sh_name_code", *([sh_arg] if sh_arg else []))
            except Exception:
                continue
            for _, r in sh.iterrows():
                code = str(r["证券代码"]).zfill(6)
                self._master[code] = {
                    "name": str(r["证券简称"]),
                    "listing_date": self._parse_date(r.get("上市日期")),
                    "market": "沪",
                    "industry": None,
                }
                self._name_index.setdefault(str(r["证券简称"]), code)
        # 深市（含所属行业）
        try:
            sz = self._fetch_df("stock_info_sz_name_code")
            for _, r in sz.iterrows():
                code = str(r["A股代码"]).strip() if pd.notna(r.get("A股代码")) else ""
                if not code or not code.isdigit():
                    continue  # 空串/非数字（NaN/占位行）不产生 "000000" 假代码
                code = code.zfill(6)
                self._master[code] = {
                    "name": str(r["A股简称"]),
                    "listing_date": self._parse_date(r.get("A股上市日期")),
                    "market": "深",
                    "industry": str(r["所属行业"]) if pd.notna(r.get("所属行业")) else None,
                }
                self._name_index.setdefault(str(r["A股简称"]), code)
        except Exception:
            pass
        # 历史曾用名（深市全市场简称变更登记）
        try:
            rn = self._fetch_df("stock_info_sz_change_name", "简称变更")
            for _, r in rn.iterrows():
                code = str(r["证券代码"]).zfill(6)
                prev = str(r["变更前简称"]) if pd.notna(r.get("变更前简称")) else None
                if prev:
                    self._name_index.setdefault(prev, code)  # 旧名 → 代码（不覆盖当前名）
                    self._historical.setdefault(code, []).append(prev)
        except Exception:
            pass
        # 退市（并入可 resolve 索引：否则历史事件复盘对退市标的全 None——幸存者偏差）
        for fn, code_col, date_col, market in [
            ("stock_info_sh_delist", "公司代码", "暂停上市日期", "沪"),
            ("stock_info_sz_delist", "证券代码", "终止上市日期", "深"),
        ]:
            try:
                df = self._fetch_df(fn) if fn == "stock_info_sh_delist" else self._fetch_df(fn, "终止上市公司")
                for _, r in df.iterrows():
                    code = str(r[code_col]).strip()
                    if not code or not code.isdigit():
                        continue
                    code = code.zfill(6)
                    name = str(r.get("公司简称") or r.get("证券简称") or "").strip()
                    self._delisted[code] = {
                        "name": name or None,
                        "delist_date": self._parse_date(r.get(date_col)),
                    }
                    if code not in self._master:  # 退市接口自带上市日期，可补 listing
                        self._master[code] = {
                            "name": name or code,
                            "listing_date": self._parse_date(r.get("上市日期")),
                            "market": market,
                            "industry": None,
                        }
                        if name:
                            self._name_index.setdefault(name, code)
            except Exception:
                pass

    @staticmethod
    def _parse_date(v) -> date_cls | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            return pd.to_datetime(v).date()
        except Exception:
            return None

    # ---- 公开接口 ----

    def resolve(self, identifier: str) -> ResolvedStock | None:
        """反幻觉核心：代码或名称（含曾用名）→ 真实标的；不存在返回 None，永不抛异常。"""
        self.ensure_loaded()
        ident = identifier.strip()
        if not ident:
            return None
        code: str | None = None
        if _is_code_like(ident):
            code = _normalize_code(ident)
        else:
            code = self._name_index.get(ident) or self._name_index.get(ident.upper())
            if code is None:
                cleaned = clean_name(ident)
                if cleaned and cleaned != ident:
                    code = self._name_index.get(cleaned) or self._name_index.get(cleaned.upper())
        if code is None or code not in self._master:
            return None
        info = self._master[code]
        return ResolvedStock(
            code=code,
            name=info["name"],
            market=info["market"],
            board=infer_board(code),
            listing_date=info["listing_date"],
            industry=info["industry"],
            is_st="ST" in info["name"],
            is_delisted=code in self._delisted,
            delist_date=(self._delisted.get(code) or {}).get("delist_date"),
            historical_names=tuple(self._historical.get(code, [])),
        )

    def is_real_at(self, code: str, on_date: date_cls) -> bool:
        """事件期是否在市：真实标的 + 上市早于 on_date + 未在 on_date 前退市。"""
        r = self.resolve(code)
        if r is None:
            return False
        if r.listing_date is not None and r.listing_date >= on_date:
            return False
        if r.is_delisted and r.delist_date is not None and r.delist_date < on_date:
            return False
        return True

    def constituents(self, industry_keyword: str) -> list[str]:
        """行业成分股（降级）：仅深市 + 交易所所属行业关键词过滤，作 hint。"""
        self.ensure_loaded()
        kw = industry_keyword.strip()
        if not kw:
            return []
        return [
            code
            for code, info in self._master.items()
            if info.get("industry") and kw in info["industry"]
        ]
