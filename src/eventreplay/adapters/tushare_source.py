"""TushareSource：tushare pro 申万三级成分股（权威召回源）。

比 akshare 的 index_component_sw / sw_index_third_cons 可靠（那两个在 akshare 里有 bug）。
无 token / 网络失败 → 返回空（CandidateGen 自动降级到 curated ThemeIndex 兜底）。
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Protocol


def _stale(df) -> bool:
    """行情缓存是否过期：最后日期落后今天超 3 个日历日（跨周末/小长假仍算新鲜）。"""
    import datetime as _dt

    if df is None or getattr(df, "empty", True) or "date" not in df.columns:
        return True
    try:
        last = max(df["date"])
        return (_dt.date.today() - last).days > 3
    except Exception:
        return True


class ConstituentSource(Protocol):
    def lookup(self, text: str) -> list[dict]:
        """文本 → [{code, theme}]（命中的成分股）。"""
        ...


class TushareSource:
    """申万三级行业成分股（index_classify + index_member）。"""

    def __init__(self, token: str | None = None, cache_dir: str | Path = "cache/tushare") -> None:
        self.token = token or os.getenv("TUSHARE_TOKEN")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._pro = None
        self._l3: list[dict] | None = None
        # 成分股持久缓存：跨进程复用（申万三级变动极慢，预拉一次即可）
        self._member_cache: dict[str, set[str]] = self._load_disk_members()
        self._check_staleness()  # 过期警告（不影响使用，只是提醒刷新）
        self._reverse_map: dict[str, str] | None = None
        self._l1_map: dict[str, str] | None = None

    def classify(self, code: str) -> str | None:
        """标的 → 申万行业名（三级优先 → L1 兜底 via stock_basic，100%覆盖）。"""
        if self._reverse_map is None:
            self._build_reverse_map()
        l3 = self._reverse_map.get(code)
        if l3:
            return l3
        if self._l1_map is None:
            self._l1_map = self.stock_industry_l1()
        return self._l1_map.get(code)

    def classify_l1(self, code: str) -> str | None:
        """标的 → 申万一级行业名（从 L1 index_member 反转，与 industry_index_codes 同口径）。"""
        if self._l1_map is None:
            self._l1_map = self._build_l1_from_members()
        return self._l1_map.get(code)

    def _build_l1_from_members(self) -> dict[str, str]:
        """从 31 个 L1 行业 index_member 建 code → L1 名映射（与 index_codes 同口径）。"""
        result: dict[str, str] = {}
        for l1_name, ic in self.industry_index_codes().items():
            for code in self._members(ic):
                if code not in result:
                    result[code] = l1_name
        return result

    def _build_reverse_map(self) -> None:
        """从 L3 classify + member cache 建 code → 三级行业名 反向映射。"""
        self._reverse_map = {}
        for ind in self._load_l3():
            ic, name = ind["index_code"], ind["industry_name"]
            for code in self._member_cache.get(ic, set()):
                if code not in self._reverse_map:  # 首个行业优先（跨行业标的罕见）
                    self._reverse_map[code] = name

    # ---- stock_basic 行业分类（L1，100% 覆盖）+ 行业指数日线 ----

    def stock_industry_l1(self) -> dict[str, str]:
        """全 A 股 code → 申万一级行业名（stock_basic，100% 覆盖）。缓存到磁盘。"""
        p = self.cache_dir / "stock_industry.pkl"
        if p.exists():
            try:
                return pickle.loads(p.read_bytes())
            except Exception:
                p.unlink(missing_ok=True)
        pro = self._api()
        if pro is None:
            return {}
        try:
            df = pro.stock_basic(fields="ts_code,name,industry")
            result = {str(r["ts_code"]).split(".")[0].zfill(6): r["industry"] for _, r in df.iterrows() if r["industry"]}
            p.write_bytes(pickle.dumps(result))
            return result
        except Exception:
            return {}

    def industry_index_codes(self) -> dict[str, str]:
        """申万一级行业名 → 指数代码（index_classify L1）。缓存。"""
        p = self.cache_dir / "sw_l1_codes.pkl"
        if p.exists():
            try:
                return pickle.loads(p.read_bytes())
            except Exception:
                p.unlink(missing_ok=True)
        pro = self._api()
        if pro is None:
            return {}
        try:
            df = pro.index_classify(level="L1", src="SW2021")
            result = {r["industry_name"]: r["index_code"] for _, r in df.iterrows()}
            p.write_bytes(pickle.dumps(result))
            return result
        except Exception:
            return {}

    def fetch_industry_index_daily(self, index_code: str) -> "pd.DataFrame | None":
        """拉申万一级行业指数日线（pro.index_daily）。缓存到磁盘，落后超 3 日自动刷新。"""
        import pandas as pd

        p = self.cache_dir / f"idx_{index_code.replace('.', '_')}.pkl"
        cached = None
        if p.exists():
            try:
                cached = pd.read_pickle(p)
            except Exception:
                p.unlink(missing_ok=True)
        if cached is not None and not _stale(cached):
            return cached
        pro = self._api()
        if pro is None:
            return cached
        try:
            df = pro.index_daily(ts_code=index_code, start_date="20150101")
            if "trade_date" in df.columns:
                df["date"] = pd.to_datetime(df["trade_date"]).dt.date
                df = df.sort_values("date").reset_index(drop=True)
            p.write_bytes(pickle.dumps(df))
            return df
        except Exception:
            return cached

    def _load_disk_members(self) -> dict[str, set[str]]:
        """从磁盘 pickle 加载已缓存的成分股（跨进程复用）。"""
        p = self.cache_dir / "sw_members.pkl"
        if p.exists():
            try:
                raw = pickle.loads(p.read_bytes())
                return {k: set(v) for k, v in raw.items()}
            except Exception:
                p.unlink(missing_ok=True)
        return {}

    def _save_disk_members(self) -> None:
        """持久化成分股缓存到磁盘。"""
        try:
            raw = {k: sorted(v) for k, v in self._member_cache.items()}
            (self.cache_dir / "sw_members.pkl").write_bytes(pickle.dumps(raw))
        except Exception:
            pass  # 缓存写失败不影响主流程

    def _check_staleness(self, threshold_days: int = 90) -> None:
        """检查成分股缓存是否过期（>90 天 → stderr 警告，不影响使用）。"""
        import sys
        import time

        p = self.cache_dir / "sw_members.pkl"
        if not p.exists() or not self._member_cache:
            return  # 无缓存或空缓存，不检查
        age_days = (time.time() - p.stat().st_mtime) / 86400
        if age_days > threshold_days:
            print(
                f"[warn] tushare 申万成分股缓存已 {age_days:.0f} 天未更新，"
                f"建议运行 `eventreplay refresh-sw` 刷新",
                file=sys.stderr,
            )

    def _api(self):
        if self._pro is None and self.token:
            try:
                import tushare as ts

                ts.set_token(self.token)
                self._pro = ts.pro_api()
            except Exception:
                self._pro = None
        return self._pro

    def _load_l3(self) -> list[dict]:
        if self._l3 is not None:
            return self._l3
        cache = self.cache_dir / "sw_l3.pkl"
        if cache.exists():
            try:
                self._l3 = pickle.loads(cache.read_bytes())
                return self._l3
            except Exception:
                cache.unlink(missing_ok=True)
        pro = self._api()
        if pro is None:
            self._l3 = []
            return self._l3
        try:
            df = pro.index_classify(level="L3", src="SW2021")
            self._l3 = [
                {"index_code": r["index_code"], "industry_name": r["industry_name"]}
                for _, r in df.iterrows()
            ]
            cache.write_bytes(pickle.dumps(self._l3))
        except Exception:
            self._l3 = []
        return self._l3

    def _members(self, index_code: str) -> set[str]:
        if index_code in self._member_cache:
            return self._member_cache[index_code]
        pro = self._api()
        if pro is None:
            return set()
        # pro.index_member 偶发瞬时网络挂起（无库级超时）→ daemon 线程 + join 超时，避免卡死整个 replay
        import threading

        # None = 拉取失败/超时（不落缓存，下次重试）；set() = 真实结果（可为空，允许缓存）
        box: dict[str, set[str] | None] = {"codes": None}

        def _fetch() -> None:
            try:
                m = pro.index_member(index_code=index_code)
                if "out_date" in m.columns:
                    cur = m[m["out_date"].isna() | (m["out_date"] == "")]
                else:
                    cur = m
                box["codes"] = {str(c).split(".")[0].zfill(6) for c in cur["con_code"]}
            except Exception:
                box["codes"] = None

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=60)  # 60s 超时；超时 t.is_alive() → box 保持 None（降级，不卡死）
        codes = box["codes"]
        if codes is None:
            return set()
        self._member_cache[index_code] = codes
        self._save_disk_members()  # 持久化到磁盘，下次进程零网络
        return codes

    def lookup(self, text: str) -> list[dict]:
        """文本命中申万行业名（三级+一级）→ 该行业当前成分股。

        三级优先（精确），一级兜底（chain LLM 常用 l1 名如'房地产'/'非银金融'/'银行'时也能匹配）。
        """
        if not text:
            return []
        out: list[dict] = []
        # 三级行业名匹配（精确）
        for ind in self._load_l3():
            name = ind.get("industry_name") or ""
            if len(name) >= 2 and name in text:
                for code in self._members(ind["index_code"]):
                    out.append({"code": code, "theme": f"申万:{name}"})
        # 一级行业名匹配（兜底：chain 常用 l1 名）
        for l1_name, ic in self.industry_index_codes().items():
            if len(l1_name) >= 2 and l1_name in text:
                for code in self._members(ic):
                    out.append({"code": code, "theme": f"申万L1:{l1_name}"})
        return out
