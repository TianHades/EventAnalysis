# -*- coding: utf-8 -*-
"""2026-09-06 工行/农行定增注资事件前瞻评估——数据拉取 spike。

拉取：
1. 工行/农行现状：最新收盘价（不复权）、2026YTD、近 60 交易日走势
2. 三个先例事件的市场反应（后复权收益，vs 沪深300 超额）：
   - 2025-03-30（周日）四大行注资公告 → t0=2025-03-31：中行/建行/交行/邮储 + 工行/农行（对照）
   - 2023-10-11（周三晚）汇金增持四大行 → t0=2023-10-12
   - 2018-03-12（周一晚）农行 1000 亿定增 → t0=2018-03-13
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter

qa = AkshareQuoteAdapter()

HS300 = "sh000300"
BENCH: dict[date, float] = {}


def load_bench(start: date, end: date) -> None:
    df = qa.index_daily(HS300, start, end)
    for _, r in df.iterrows():
        BENCH[r["date"]] = float(r["close"])


def cum_ret(series: list[tuple[date, float]], i_base: int, i_tgt: int) -> float:
    return series[i_tgt][1] / series[i_base][1] - 1.0


def event_reaction(name: str, code: str, t0: date, win_start: date, win_end: date) -> None:
    """t0=公告后首个交易日。报告 t0 当日、+1/+3/+5/+10/+20 交易日累计收益与超额。"""
    df = qa.daily_adjusted(code, win_start, win_end, adjust="hfq")
    rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
    idx0 = next((i for i, (d, _) in enumerate(rows) if d >= t0), None)
    if idx0 is None or idx0 == 0:
        print(f"  {code}: 窗口内无 t0 前数据")
        return
    base = idx0 - 1  # 公告前最后一日收盘
    print(f"  {code}: base={rows[base][0]} close_ret(t0)={cum_ret(rows, base, idx0):+.2%}", end="")
    for n in (1, 3, 5, 10, 20):
        i = min(idx0 + n, len(rows) - 1)
        ar = cum_ret(rows, base, i)
        bd = rows[i][0]
        # 超额：同窗口基准
        j0 = BENCH.get(rows[base][0])
        j1 = BENCH.get(bd)
        if j0 and j1:
            er = ar - (j1 / j0 - 1.0)
            print(f" | +{n}d({bd.strftime('%m-%d')}) {ar:+.2%} (超额{er:+.2%})", end="")
        else:
            print(f" | +{n}d({bd.strftime('%m-%d')}) {ar:+.2%}", end="")
    print()


def current_state(code: str, name: str) -> None:
    df_r = qa.daily_adjusted(code, date(2025, 12, 1), date.today(), adjust="hfq")
    rows = [(r["date"], float(r["close"])) for _, r in df_r.iterrows()]
    ytd = rows[-1][1] / rows[0][1] - 1.0
    d60 = rows[-1][1] / rows[max(0, len(rows) - 60)][1] - 1.0
    df_p = qa.daily_adjusted(code, date.today().replace(month=1, day=1), date.today(), adjust="")
    last = df_p.iloc[-1] if not df_p.empty else None
    px = f"{float(last['close']):.2f}元({last['date']})" if last is not None else "n/a"
    print(f"  {name}({code}): 最新不复权收盘 {px}, 2026YTD {ytd:+.2%}, 近60交易日 {d60:+.2%}")


print("=== A. 工行/农行现状（截至 2026-09-04 收盘） ===")
load_bench(date(2025, 12, 1), date.today())
for code, nm in (("601398", "工商银行"), ("601288", "农业银行")):
    current_state(code, nm)

print("\n=== B. 先例1：2025-03-30 四大行注资公告（t0=2025-03-31） ===")
load_bench(date(2025, 2, 1), date(2025, 7, 1))
for code in ("601988", "601939", "601328", "601658", "601398", "601288"):
    event_reaction("2025注资", code, date(2025, 3, 31), date(2025, 3, 1), date(2025, 5, 30))

print("\n=== C. 先例2：2023-10-11 汇金增持四大行（t0=2023-10-12） ===")
load_bench(date(2023, 9, 1), date(2023, 12, 15))
for code in ("601988", "601939", "601328", "601398"):
    event_reaction("汇金增持", code, date(2023, 10, 12), date(2023, 9, 15), date(2023, 11, 30))

print("\n=== D. 先例3：2018-03-12 农行定增1000亿（t0=2018-03-13） ===")
load_bench(date(2018, 2, 1), date(2018, 6, 1))
for code in ("601288", "601398"):
    event_reaction("农行2018", code, date(2018, 3, 13), date(2018, 3, 1), date(2018, 4, 30))

print("\n=== E. 中证银行指数 2026YTD（板块背景） ===")
try:
    df = qa.index_daily("sz399986", date(2025, 12, 1), date.today())
    print(f"  中证银行: 2026YTD {df['close'].iloc[-1] / df['close'].iloc[0] - 1:+.2%}, 最新 {df['date'].iloc[-1]}")
except Exception as exc:  # noqa: BLE001
    print(f"  中证银行拉取失败: {exc}")
