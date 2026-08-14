"""收益率与市场模型（事件研究法基础，spec §4.1-4.2）。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def simple_returns(close: pd.Series) -> pd.Series:
    """简单收益率 R_t = (P_t - P_{t-1}) / P_{t-1}，按日期对齐。"""
    return close.pct_change().dropna()


def fit_market_model(
    stock_ret: np.ndarray, bench_ret: np.ndarray
) -> tuple[float, float, float]:
    """OLS 拟合市场模型 R_i = α + β·R_m（估计窗），返回 (α̂, β̂, σ_AR)。

    σ_AR 为估计窗残差标准差（ddof=2，扣除 α、β 两个参数）。
    输入须为等长对齐的估计窗收益序列。
    """
    if len(stock_ret) < 2:
        raise ValueError("估计窗样本不足")
    X = np.column_stack([np.ones_like(bench_ret), bench_ret])
    coef, *_ = np.linalg.lstsq(X, stock_ret, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    resid = stock_ret - X @ coef
    sigma = float(np.std(resid, ddof=2)) if len(resid) > 2 else float(np.std(resid))
    return alpha, beta, sigma


def abnormal_returns(
    stock_ret: np.ndarray, bench_ret: np.ndarray, alpha: float, beta: float
) -> np.ndarray:
    """AR_t = R_i,t − (α̂ + β̂·R_m,t)。"""
    return stock_ret - (alpha + beta * bench_ret)


def car(ar: np.ndarray, start: int, end: int) -> float:
    """CAR(W) = Σ_{t∈[start,end]} AR_t（含两端）。"""
    if start < 0 or end >= len(ar) or start > end:
        return float("nan")
    return float(ar[start : end + 1].sum())
