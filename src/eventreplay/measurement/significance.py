"""显著性（spec §4.3）：

- 单标的经济显著性为主（|CAR[0,+20]| 阈值）。
- 组合层面：KP(2010) Adjusted BMP（参数）+ 广义秩检验（非参数，Van der Waerden 正态分，
  作为 KP(2011) GRANK 的 MVP 实现，需以 §9 蒙特卡洛 size 检验校准）。
- GRANK 手写（spike：event-study-toolkit 不暴露可用实现）。
- 注意：当前 AttributionFiltering 主线未调用本模块组合统计（用横截面 CAR[0,+20] 简化 t 检验，
  见 attribution.py）；本模块留作 SAR 矩阵贯通后的升级路径，组合统计量仅作辅证/披露。
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm, rankdata, shapiro

from ..schema.common import Board

_ECON_THRESHOLD = {
    Board.CREATIVE.value: 0.20,
    Board.STAR.value: 0.20,
    Board.MAIN.value: 0.10,
    Board.BSE.value: 0.30,
    Board.HK_MAIN.value: 0.15,  # 港股无涨跌停+日波动大，10%过低→15%
}


def economic_threshold(board: Board | str | None) -> float:
    """经济显著性 |CAR[0,+20]| 阈值（与涨跌停量级对齐，spec §4.3）。"""
    if board is None:
        return 0.10
    b = board.value if isinstance(board, Board) else board
    return _ECON_THRESHOLD.get(b, 0.10)


def economic_significant(car_0_20: float, board: Board | str | None, *, n: int) -> bool:
    """单标的经济显著性过滤（绝对阈值；n 仅保留签名兼容，阈值不随 n 变化）。"""
    return abs(car_0_20) >= economic_threshold(board)


def _mean_cross_corr(sar: np.ndarray) -> float:
    """sar 行间平均两两截面相关 rbar（KP 截面相关修正的输入）。

    corrcoef 对零方差行产出 NaN → np.nan_to_num 钳位为 0，避免 NaN 传播到 Z/p。
    rbar 钳位到 [-0.5, 0.99] 防止修正因子 √(1+(L−1)r̄) 非正/爆炸。
    """
    N, L = sar.shape
    if N < 2 or L < 2:
        return 0.0
    corr = np.nan_to_num(np.corrcoef(sar))
    rbar = float((corr.sum() - N) / (N * (N - 1)))
    return max(min(rbar, 0.99), -0.5)


def adjusted_bmp(sar: np.ndarray) -> tuple[float, float, float]:
    """KP(2010) Adjusted BMP：BMP 方差调整 + 截面相关调整。

    sar: (N, L) 标准化 AR 矩阵（事件窗）。
    返回 (Z_adj, p_value, rbar)。rbar 为平均截面相关。
    """
    N, L = sar.shape
    if N < 1 or L < 1:
        return float("nan"), float("nan"), 0.0
    cum = sar.sum(axis=1)
    V = sar.std(axis=1, ddof=1) if L > 1 else np.zeros(N)
    V = np.where(V > 1e-12, V, 1.0)
    bmp_i = cum / (np.sqrt(L) * V)
    Z_bmp = float(bmp_i.mean() * np.sqrt(N)) if N > 0 else float("nan")
    rbar = _mean_cross_corr(sar)
    Z_adj = Z_bmp / math.sqrt(max(1 + (L - 1) * rbar, 1e-9))
    p = float(2 * (1 - norm.cdf(abs(Z_adj))))
    return Z_adj, p, rbar


def generalized_rank_z(sar: np.ndarray) -> tuple[float, float]:
    """广义秩检验（Van der Waerden 正态分；作为 KP(2011) GRANK 的 MVP 实现）。

    sar: (N, L)。对 |SAR| 池化秩 → 正态分 q；按 sign(SAR) 加和。
    截面相关修正（KP(2011) 核心）：Z 除以 √(1+(L−1)r̄)，其中 rbar 为 sar 行间
    平均两两相关（与 adjusted_bmp 同口径）。注意 rbar 用事件窗 SAR 矩阵估计——
    事件窗含信号会高估 rbar（修正偏保守）；理想口径应来自估计窗 SAR，MVP 从简。
    返回 (Z, p_value)。需以 size 检验校准（spec §9）。
    """
    N, L = sar.shape
    M = N * L
    if M < 2:
        return float("nan"), float("nan")
    absS = np.abs(sar).flatten()
    # 平均秩（并列取平均，与注释口径一致）
    ranks = rankdata(absS, method="average")
    # Van der Waerden 正态分
    q = norm.ppf(ranks / (M + 1))
    signs = np.sign(sar.flatten())
    signed = signs * q
    # 池化 Z：sum(signed) / sqrt(sum(q^2))，~N(0,1) under H0（截面独立时）
    denom = math.sqrt(float((q**2).sum()))
    if denom <= 0:
        return 0.0, 1.0
    Z = float(signed.sum() / denom)
    # 截面相关修正：未修正的 Z 在共同因子下假显著（MC ρ=0.3 时拒绝率 ~0.4 vs 名义 0.05）
    rbar = _mean_cross_corr(sar)
    Z = Z / math.sqrt(max(1 + (L - 1) * rbar, 1e-9))
    p = float(2 * (1 - norm.cdf(abs(Z))))
    return Z, p


def choose_pool_stat(sar: np.ndarray) -> dict:
    """自适应选择主推断（spec §4.3）。

    N<5 → 仅记录不披露；N≥30 + 正态 + 低截面相关 → 主推断 Adjusted BMP；否则广义秩。
    分歧时同时披露两 p 值。
    """
    N, L = sar.shape
    z_adj, p_adj, rbar = adjusted_bmp(sar)
    z_rk, p_rk = generalized_rank_z(sar)
    disclosed = N >= 5
    if not disclosed:
        return {
            "n": int(N),
            "adj_bmp_z": z_adj,
            "adj_bmp_p": p_adj,
            "grank_z": z_rk,
            "grank_p": p_rk,
            "disclosed": False,
            "disagreement": False,
            "main": None,
            "rbar": rbar,
        }
    benign = False
    if N >= 30:
        try:
            # 正态性：池化 SAR 的 Shapiro-Wilk
            flat = sar.flatten()
            if len(flat) >= 3:
                _, p_norm = shapiro(flat)
                benign = bool(p_norm > 0.05) and rbar < 0.1
        except Exception:
            benign = False
    main = "adj_bmp" if benign else "grank"
    main_p = p_adj if main == "adj_bmp" else p_rk
    disagreement = bool((p_adj < 0.05) != (p_rk < 0.05))
    return {
        "n": int(N),
        "adj_bmp_z": z_adj,
        "adj_bmp_p": p_adj,
        "grank_z": z_rk,
        "grank_p": p_rk,
        "disclosed": True,
        "disagreement": disagreement,
        "main": main,
        "main_p": float(main_p),
        "rbar": rbar,
    }
