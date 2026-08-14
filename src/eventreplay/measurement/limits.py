"""涨跌停判定（按事件窗每日生效规则查表，spec §4.4）。

生效日表（spike + 史实确认）：
- 主板 10%；主板新股前5日不设限（2023-04-10 全面注册制起）
- 创业板 10%→20%（2020-08-24 起）；新股前5日不设限（2020-08-24 起）
- 科创板 20%（2019-07-22 起）；新股前5日不设限（2019-07-22 起）
- 北交所 30%（2021-11-15 起）；新股前5日不设限
- ST 主板 5%（2026-07-06 起改 10%）；ST 创业板/科创板仍 20%（不降 5%）
"""

from __future__ import annotations

from datetime import date

from ..schema.common import Board

_REGISTERED_MAIN_NEW = date(2023, 4, 10)
_CREATIVE_20 = date(2020, 8, 24)
_STAR_OPEN = date(2019, 7, 22)
_BSE_OPEN = date(2021, 11, 15)
_ST_MAIN_10 = date(2026, 7, 6)


def daily_limit_pct(
    board: Board | str,
    *,
    is_st: bool,
    is_new_first5: bool,
    d: date,
) -> float | None:
    """返回当日涨跌幅限制（小数，如 0.10）；None 表示不设限。

    is_new_first5：是否处于上市前 5 个交易日内（由调用方结合上市日与交易日历判定）。
    """
    b = board.value if isinstance(board, Board) else board

    if b == Board.MAIN.value:
        if is_new_first5 and d >= _REGISTERED_MAIN_NEW:
            return None
        if is_st:
            return 0.10 if d >= _ST_MAIN_10 else 0.05
        return 0.10

    if b == Board.CREATIVE.value:
        if is_new_first5 and d >= _CREATIVE_20:
            return None
        base = 0.20 if d >= _CREATIVE_20 else 0.10
        return base  # ST 创业板仍 20%

    if b == Board.STAR.value:
        if is_new_first5 and d >= _STAR_OPEN:
            return None
        return 0.20  # ST 科创板仍 20%

    if b == Board.BSE.value:
        if is_new_first5 and d >= _BSE_OPEN:
            return None
        return 0.30

    if b == Board.HK_MAIN.value:
        return None  # 港股无涨跌停（Phase 5）

    return None


def is_within_new_listing_first5(
    listing_date: date | None, d: date, trade_dates_on_after: int
) -> bool:
    """d 是否处于上市后前 5 个交易日内。

    trade_dates_on_after：上市日（含）到 d（含）之间的交易日数（由调用方按交易日历算）。
    """
    if listing_date is None or d < listing_date:
        return False
    return 1 <= trade_dates_on_after <= 5


def limit_hit(pct_change: float, limit_pct: float | None, tol: float = 1e-4) -> str | None:
    """根据当日涨跌幅与限制，判定 'up' / 'down' / None。

    pct_change：当日收益率（close/prev_close - 1）。
    """
    if limit_pct is None:
        return None
    if pct_change >= limit_pct - tol:
        return "up"
    if pct_change <= -(limit_pct - tol):
        return "down"
    return None
