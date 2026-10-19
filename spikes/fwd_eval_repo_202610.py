# -*- coding: utf-8 -*-
"""2026-10-01 央行 1.2 万亿买断式逆回购（3M，净投放 2000 亿）前瞻评估——数据拉取 spike。

9-30 盘后公告、10-08（节后首日）操作，t0 = 2026-10-08。本脚本拉取：
1. 规律库 find_similar 向量检索（事件描述 × Pattern 库）
2. 当前市场状态（股指/国债ETF/券商ETF，截至 2026-09-30 收盘，抢跑检测）
3. 买断式逆回购历史节点先例反应（公告/操作日 → t0/+5/+20）：
   - 2024-10-28 工具创设公告（超预期成分）
   - 2024-10-31 首次操作 5000 亿
   - 2024-12-31 1.4 万亿（当时最大月度量）
   - 2025-01-27 1.7 万亿（史上最大月度量）
   - 2025-06-05 打破月末惯例、月初提前公告（当月净投放 2000 亿）
   - 2026-01-08 1.1 万亿等量续作
   - 2026-09-07 9 月等量续作（本轮净投放拐点的对照基线）
4. 国庆后首个交易日季节性（2016-2025，上证指数）
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

import akshare as ak  # noqa: E402

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter  # noqa: E402
from eventreplay.llm import Embedder  # noqa: E402
from eventreplay.pipeline.pattern_service import PatternService  # noqa: E402
from eventreplay.storage import Storage  # noqa: E402

qa = AkshareQuoteAdapter()

HS300 = "sh000300"
BENCH: dict[date, float] = {}
ETF_CACHE: dict[str, list[tuple[date, float]]] = {}

DESC = (
    "中国人民银行公告10月8日开展12000亿元买断式逆回购操作，期限3个月（89天），"
    "当月到期1万亿元，净投放2000亿元，保持银行体系流动性充裕，配合政府债券发行放量，"
    "9月为等量续作后转向加量净投放，货币政策中期流动性投放工具"
)


def load_bench(start: date, end: date) -> None:
    df = qa.index_daily(HS300, start, end)
    for _, r in df.iterrows():
        BENCH[r["date"]] = float(r["close"])


def etf_rows(sym: str) -> list[tuple[date, float]]:
    if sym not in ETF_CACHE:
        df = ak.fund_etf_hist_sina(symbol=sym)
        ETF_CACHE[sym] = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
    return ETF_CACHE[sym]


def etf_series(sym: str, start: date, end: date) -> list[tuple[date, float]]:
    return [(d, c) for d, c in etf_rows(sym) if start <= d <= end]


def cum_ret(series: list[tuple[date, float]], i_base: int, i_tgt: int) -> float:
    return series[i_tgt][1] / series[i_base][1] - 1.0


def bench_cum(d0: date, d1: date) -> float | None:
    j0, j1 = BENCH.get(d0), BENCH.get(d1)
    return (j1 / j0 - 1.0) if j0 and j1 else None


def reaction(name: str, kind: str, t0: date, win: tuple[date, date], with_er: bool) -> None:
    """报告单标的 t0/+5/+20 反应。kind: index=qa.index_daily / etf=sina ETF。"""
    start, end = win
    if kind == "index":
        df = qa.index_daily(name, start, end)
        rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
    else:
        rows = etf_series(name, start, end)
    idx0 = next((i for i, (d, _) in enumerate(rows) if d >= t0), None)
    if idx0 is None or idx0 == 0:
        print(f"    {name}: 窗口内无数据")
        return
    base = idx0 - 1
    parts = [f"t0({rows[idx0][0].strftime('%m-%d')}) {cum_ret(rows, base, idx0):+.2%}"]
    for n in (5, 20):
        i = min(idx0 + n, len(rows) - 1)
        ar = cum_ret(rows, base, i)
        bc = bench_cum(rows[base][0], rows[i][0]) if with_er else None
        tag = f" (超额{ar - bc:+.2%})" if bc is not None else ""
        parts.append(f"+{n}d {ar:+.2%}{tag}")
    print(f"    {name}: " + " | ".join(parts))


def event_block(title: str, t0: date, span: int = 45) -> None:
    win = (t0 - timedelta(days=60), t0 + timedelta(days=75))
    print(f"  [{title}] t0={t0}")
    reaction("sh000001", "index", t0, win, with_er=False)
    reaction("sh512000", "etf", t0, win, with_er=True)
    reaction("sh511260", "etf", t0, win, with_er=False)
    reaction("sh511090", "etf", t0, win, with_er=False)
    _ = span


def current_state() -> None:
    print("=== B. 当前市场状态（截至 2026-09-30 收盘） ===")
    for kind, sym, nm in (
        ("index", "sh000001", "上证指数"),
        ("index", "sz399006", "创业板指"),
        ("etf", "sh512000", "券商ETF"),
        ("etf", "sh511260", "10年国债ETF"),
        ("etf", "sh511090", "30年国债ETF"),
    ):
        if kind == "index":
            df = qa.index_daily(sym, date(2025, 12, 1), date(2026, 10, 1))
            rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
        else:
            rows = etf_series(sym, date(2025, 12, 1), date(2026, 10, 1))
        if not rows:
            continue
        last = rows[-1]
        ytd = last[1] / rows[0][1] - 1.0
        d20 = last[1] / rows[max(0, len(rows) - 20)][1] - 1.0
        d5 = last[1] / rows[max(0, len(rows) - 5)][1] - 1.0
        print(f"  {nm}({sym}): 收 {last[1]:.2f}({last[0]}) | 近5日 {d5:+.2%} | 近20日 {d20:+.2%} | 2026YTD {ytd:+.2%}")


print("=== A. 规律库向量检索（find_similar, top 6） ===")
storage = Storage("data/eventreplay.db")
svc = PatternService(storage, reasoner=None, embedder=Embedder())  # type: ignore[arg-type]
results, degraded = svc.find_similar(DESC, top_k=6)
if degraded:
    print("  [degraded] embed 失败，仅结构化过滤")
for pat, score in results:
    q = pat.qualitative
    q5 = f"{q.magnitude}/{q.scope}/{q.persistence}/{q.significance}/{q.surprise}" if q else "n/a"
    print(f"  {score:.3f} [{pat.shock_dimension.value}|{pat.impact_direction.value}] {pat.event_type}")
    print(f"        {pat.event_summary[:90]}")
    print(f"        五维: {q5}")

current_state()

print("\n=== C. 买断式逆回购历史节点先例反应（t0/+5/+20；国债ETF为绝对收益，券商ETF vs 沪深300超额） ===")
CASES = [
    ("2024-10-28 创设公告", date(2024, 10, 28)),
    ("2024-10-31 首次操作5000亿", date(2024, 10, 31)),
    ("2024-12-31 1.4万亿(当时最大)", date(2024, 12, 31)),
    ("2025-01-27 1.7万亿(史上最大)", date(2025, 1, 27)),
    ("2025-06-05 月初提前公告(净投放2000亿)", date(2025, 6, 5)),
    ("2026-01-08 1.1万亿等量续作", date(2026, 1, 8)),
    ("2026-09-07 9月等量续作", date(2026, 9, 7)),
]
for title, t0 in CASES:
    load_bench(t0 - timedelta(days=60), t0 + timedelta(days=75))
    event_block(title, t0)

print("\n=== D. 国庆后首个交易日季节性（上证指数，2016-2025） ===")
df = qa.index_daily("sh000001", date(2016, 1, 1), date(2026, 10, 1))
rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
for yr in range(2016, 2026):
    sep_last = [i for i, (d, _) in enumerate(rows) if d.year == yr and d.month == 9]
    oct_first = [i for i, (d, _) in enumerate(rows) if d.year == yr and d.month == 10]
    if not sep_last or not oct_first:
        continue
    i0, i1 = sep_last[-1], oct_first[0]
    if i1 <= i0:
        continue
    chg = rows[i1][1] / rows[i0][1] - 1.0
    pre10 = rows[i0][1] / rows[max(0, i0 - 10)][1] - 1.0
    print(f"  {yr}: 节前最后日 {rows[i0][0]} → 节后首日 {rows[i1][0]} {chg:+.2%}（节前10日 {pre10:+.2%}）")
