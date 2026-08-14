"""持续性指标（spec §4.4）。"""

from __future__ import annotations

import numpy as np


def persistence_ratio(car_60: float | None, car_5: float | None) -> float | None:
    """长/短窗 CAR 比 CAR[0,+60]/CAR[0,+5]（持续性主指标）。

    分母绝对值 <2% 时返回 None（绝对量级太小，比值无意义）。
    """
    if car_5 is None or car_60 is None or abs(car_5) < 0.02:
        return None
    return car_60 / car_5


def sig_ar_streak(ar_evt: np.ndarray, sigma: float, threshold: float = 1.96) -> int:
    """显著 AR 最长连续天数（标的内时序，禁止跨标的排序，spec §4.3/§4.4）。"""
    if sigma <= 0 or len(ar_evt) == 0:
        return 0
    sig = np.abs(ar_evt) / sigma > threshold
    longest = run = 0
    for s in sig:
        run = run + 1 if s else 0
        longest = max(longest, run)
    return int(longest)


def aav_days(
    vol_evt: np.ndarray, vol_est_mean: float, vol_est_std: float, threshold: float = 2.0
) -> int:
    """异常成交量持续天数（事件窗内 AAV>2.0 的天数，spec §4.4）。"""
    if vol_est_std <= 0 or len(vol_evt) == 0:
        return 0
    aav = (vol_evt - vol_est_mean) / vol_est_std
    return int((aav > threshold).sum())
