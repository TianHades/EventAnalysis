# -*- coding: utf-8 -*-
"""2026-09 假设事件前瞻评估——中国正式公布核动力航母（004 型）信息。

拉取：
1. 规律库 find_similar 向量检索（本假设事件描述 × 58 Pattern，local bge 512 维）
2. 当前状态（军工指数/船舶总装/动力/核链标的 2026YTD、近 60/20 日，抢跑检测）
3. 六个航母里程碑先例的市场反应（后复权，vs 沪深300 超额）：
   - 2012-09-25 辽宁舰入列
   - 2017-04-26 首艘国产航母（山东舰）下水
   - 2019-12-17 山东舰入列
   - 2022-06-17 福建舰下水
   - 2024-05-01 福建舰首次海试
   - 2025-11-05 福建舰入列（三亚）
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv()

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter  # noqa: E402
from eventreplay.llm import Embedder  # noqa: E402
from eventreplay.pipeline.pattern_service import PatternService  # noqa: E402
from eventreplay.storage import Storage  # noqa: E402

qa = AkshareQuoteAdapter()

HS300 = "sh000300"
JUNGONG = "sz399967"  # 中证军工
BENCH: dict[date, float] = {}
JG: dict[date, float] = {}


def _asof(store: dict[date, float], d: date) -> float | None:
    """取 ≤d 的最近一个基准收盘（不同指数最新交易日可能错位一日）。"""
    cand = [k for k in store if k <= d]
    return store[max(cand)] if cand else None

DESC = (
    "中国官方正式公布首艘核动力航空母舰（004型）信息：确认采用核动力，"
    "大连造船厂建造，预计下水。国防军工重大装备里程碑，船舶总装、核动力系统、"
    "舰载机产业链受益，技术突破与军备建设预期升温。"
)


def load_index(store: dict[date, float], code: str, start: date, end: date) -> None:
    df = qa.index_daily(code, start, end)
    for _, r in df.iterrows():
        store[r["date"]] = float(r["close"])


def cum_ret(series: list[tuple[date, float]], i_base: int, i_tgt: int) -> float:
    return series[i_tgt][1] / series[i_base][1] - 1.0


def event_reaction(code: str, name: str, t0: date, win_start: date, win_end: date) -> None:
    """t0=事件日（或后首个交易日）。输出 t0 当日、+5/+20 交易日累计收益与超额（vs 沪深300/中证军工）。"""
    try:
        df = qa.daily_adjusted(code, win_start, win_end, adjust="hfq")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}({code}): 拉取失败 {exc}")
        return
    rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
    idx0 = next((i for i, (d, _) in enumerate(rows) if d >= t0), None)
    if idx0 is None or idx0 == 0:
        print(f"  {name}({code}): 窗口内无 t0 前数据")
        return
    # 事件前 20 日（抢跑）
    i_pre = max(0, idx0 - 21)
    pre20 = cum_ret(rows, i_pre, idx0 - 1)
    base = idx0 - 1
    line = f"  {name}({code}): pre20 {pre20:+.1%} | t0 {cum_ret(rows, base, idx0):+.2%}"
    for n in (5, 20):
        i = min(idx0 + n, len(rows) - 1)
        ar = cum_ret(rows, base, i)
        bd = rows[i][0]
        j0, j1 = _asof(BENCH, rows[base][0]), _asof(BENCH, bd)
        g0, g1 = _asof(JG, rows[base][0]), _asof(JG, bd)
        er = ar - (j1 / j0 - 1.0) if j0 and j1 else float("nan")
        ev = ar - (g1 / g0 - 1.0) if g0 and g1 else float("nan")
        line += f" | +{n}d({bd.strftime('%y-%m-%d')}) {ar:+.2%}(超额HS300 {er:+.2%}/军工 {ev:+.2%})"
    print(line)


def index_reaction(code: str, name: str, t0: date, win_start: date, win_end: date) -> None:
    df = qa.index_daily(code, win_start, win_end)
    rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
    idx0 = next((i for i, (d, _) in enumerate(rows) if d >= t0), None)
    if idx0 is None or idx0 == 0:
        print(f"  {name}: 窗口内无 t0 前数据")
        return
    i_pre = max(0, idx0 - 21)
    base = idx0 - 1
    line = f"  {name}: pre20 {cum_ret(rows, i_pre, base):+.1%} | t0 {cum_ret(rows, base, idx0):+.2%}"
    for n in (5, 20):
        i = min(idx0 + n, len(rows) - 1)
        ar = cum_ret(rows, base, i)
        j0, j1 = _asof(BENCH, rows[base][0]), _asof(BENCH, rows[i][0])
        er = ar - (j1 / j0 - 1.0) if j0 and j1 else float("nan")
        line += f" | +{n}d {ar:+.2%}(超额 {er:+.2%})"
    print(line)


def current_state(code: str, name: str) -> None:
    df = qa.daily_adjusted(code, date(2025, 12, 1), date.today(), adjust="hfq")
    rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
    if not rows:
        print(f"  {name}({code}): 无数据")
        return
    ytd = rows[-1][1] / rows[0][1] - 1.0
    d60 = rows[-1][1] / rows[max(0, len(rows) - 60)][1] - 1.0
    i20 = max(0, len(rows) - 21)
    d20 = rows[-1][1] / rows[i20][1] - 1.0
    j0, j1 = _asof(BENCH, rows[i20][0]), _asof(BENCH, rows[-1][0])
    er20 = d20 - (j1 / j0 - 1.0) if j0 and j1 else float("nan")
    print(
        f"  {name}({code}): 2026YTD {ytd:+.1%}, 近60日 {d60:+.1%}, 近20日 {d20:+.1%}"
        f"(超额 {er20:+.1%}), 最新 {rows[-1][0]}"
    )


print("=== A. 规律库向量检索（find_similar, top 6） ===")
storage = Storage("data/eventreplay.db")
svc = PatternService(storage, reasoner=None, embedder=Embedder())  # type: ignore[arg-type]
results, degraded = svc.find_similar(DESC, top_k=6)
if degraded:
    print("  [degraded] embed 失败，仅结构化过滤")
for pat, score in results:
    q = pat.qualitative
    q5 = (
        f"{q.magnitude}/{q.scope}/{q.persistence}/{q.significance}/{q.surprise}"
        if q
        else "n/a"
    )
    print(f"  {score:.3f} [{pat.shock_dimension.value}|{pat.impact_direction.value}] {pat.event_type}")
    print(f"        {pat.event_summary[:90]}")
    print(f"        五维: {q5}")

print("\n=== B. 当前状态（截至最新收盘） ===")
load_index(BENCH, HS300, date(2025, 12, 1), date.today())
load_index(JG, JUNGONG, date(2025, 12, 1), date.today())
df = qa.index_daily(JUNGONG, date(2025, 12, 1), date.today())
rows = [(r["date"], float(r["close"])) for _, r in df.iterrows()]
ytd = rows[-1][1] / rows[0][1] - 1.0
d20 = rows[-1][1] / rows[max(0, len(rows) - 21)][1] - 1.0
j0, j1 = _asof(BENCH, rows[max(0, len(rows) - 21)][0]), _asof(BENCH, rows[-1][0])
print(
    f"  中证军工(sz399967): 2026YTD {ytd:+.1%}, 近20日 {d20:+.1%}"
    f"(超额 {d20 - (j1 / j0 - 1.0):+.1%}), 最新 {rows[-1][0]}"
)
for code, nm in (
    ("600150", "中国船舶"),
    ("600482", "中国动力"),
    ("600685", "中船防务"),
    ("600760", "中航沈飞"),
    ("000777", "中核科技"),
    ("603308", "应流股份"),
):
    try:
        current_state(code, nm)
    except Exception as exc:  # noqa: BLE001
        print(f"  {nm}({code}): 失败 {exc}")

EVENTS: list[tuple[str, date, date, date, list[tuple[str, str]]]] = [
    (
        "C. 先例1：2012-09-25 辽宁舰入列",
        date(2012, 9, 25),
        date(2012, 8, 1),
        date(2012, 12, 31),
        [("600150", "中国船舶"), ("600685", "中船防务"), ("600072", "中船科技")],
    ),
    (
        "D. 先例2：2017-04-26 首艘国产航母下水",
        date(2017, 4, 26),
        date(2017, 3, 1),
        date(2017, 8, 31),
        [("600150", "中国船舶"), ("601989", "中国重工"), ("600685", "中船防务"), ("600482", "中国动力")],
    ),
    (
        "E. 先例3：2019-12-17 山东舰入列",
        date(2019, 12, 17),
        date(2019, 11, 1),
        date(2020, 3, 31),
        [("600150", "中国船舶"), ("601989", "中国重工"), ("600482", "中国动力"), ("600760", "中航沈飞")],
    ),
    (
        "F. 先例4：2022-06-17 福建舰下水",
        date(2022, 6, 17),
        date(2022, 5, 1),
        date(2022, 10, 31),
        [("600150", "中国船舶"), ("601989", "中国重工"), ("600482", "中国动力"), ("600760", "中航沈飞"), ("000777", "中核科技")],
    ),
    (
        "G. 先例5：2024-05-01 福建舰首次海试",
        date(2024, 5, 1),
        date(2024, 4, 1),
        date(2024, 8, 31),
        [("600150", "中国船舶"), ("601989", "中国重工"), ("600482", "中国动力"), ("600760", "中航沈飞"), ("000777", "中核科技")],
    ),
    (
        "H. 先例6：2025-11-05 福建舰入列（三亚）",
        date(2025, 11, 5),
        date(2025, 10, 1),
        date(2026, 1, 31),
        [("600150", "中国船舶"), ("600482", "中国动力"), ("600760", "中航沈飞"), ("000777", "中核科技")],
    ),
]

for title, t0, ws, we, stocks in EVENTS:
    print(f"\n=== {title} ===")
    load_index(BENCH, HS300, ws, we)
    load_index(JG, JUNGONG, ws, we)
    index_reaction(JUNGONG, "中证军工", t0, ws, we)
    for code, nm in stocks:
        try:
            event_reaction(code, nm, t0, ws, we)
        except Exception as exc:  # noqa: BLE001
            print(f"  {nm}({code}): 失败 {exc}")
