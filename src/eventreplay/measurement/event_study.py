"""事件研究法单标的度量流水线（spec §4）。

输入个股 OHLCV + 基准 + reaction_date → 输出 ImpactMeasurement（含 CAR/持续性/涨跌停/低置信）。
"""

from __future__ import annotations

from datetime import date as date_cls

import numpy as np
import pandas as pd

from ..schema.common import Board, StockDirection, Tier
from ..schema.measurement import ImpactMeasurement
from .cleaning import clean_quotes
from .limits import daily_limit_pct, limit_hit
from .persistence import aav_days, persistence_ratio, sig_ar_streak
from .returns import abnormal_returns, car, fit_market_model, simple_returns

_EST_LEFT, _EST_RIGHT = 120, 21  # 估计窗 [t-120, t-21]


def _align(stock: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    s = stock.copy()
    b = bench.copy()
    s["date"] = pd.to_datetime(s["date"]).dt.date
    b["date"] = pd.to_datetime(b["date"]).dt.date
    m = s.merge(b[["date", "close"]], on="date", how="inner", suffixes=("", "_bench"))
    return m.sort_values("date").reset_index(drop=True)


def measure_stock(
    *,
    stock_id: str,
    episode_id: str,
    quotes: pd.DataFrame,
    bench: pd.DataFrame,
    reaction_date: date_cls,
    board: Board | str | None,
    is_st: bool,
    listing_date: date_cls | None,
    direction: StockDirection | None = None,
    bench_industry: pd.DataFrame | None = None,
) -> ImpactMeasurement | None:
    """度量单标的×单子事件。返回 None 表示该标的应 skip（数据不可用/新股）。"""
    cleaned = clean_quotes(
        quotes,
        listing_date=listing_date,
        reaction_date=reaction_date,
        estimation_window_days=_EST_LEFT,
    )
    if cleaned is None:
        return None
    df = _align(cleaned.cleaned, bench)
    if df.empty or "close" not in df.columns or "close_bench" not in df.columns:
        return None

    # 合并行业指数 close（双层基准：行业 CAR 剥离板块 β）
    if bench_industry is not None and not bench_industry.empty:
        ind = bench_industry[["date", "close"]].copy()
        ind["date"] = pd.to_datetime(ind["date"]).dt.date
        ind = ind.rename(columns={"close": "close_industry"})
        df = df.merge(ind, on="date", how="left")
        # 仅填补内部缺口：行业指数实际覆盖范围之外不得外推（ffill/bfill 冻结价格会伪造基准）
        _have = df["close_industry"].notna()
        if _have.any():
            _first = df.loc[_have, "date"].iloc[0]
            _last = df.loc[_have, "date"].iloc[-1]
            df["close_industry"] = df["close_industry"].ffill().where(df["date"].between(_first, _last))

    # t0：reaction_date 当日或之后首个交易日
    on_after = df.loc[df["date"] >= reaction_date]
    if on_after.empty:
        return None
    t0 = int(on_after.index[0])

    est_lo, est_hi = t0 - _EST_LEFT, t0 - _EST_RIGHT
    est_start = max(est_lo, 0)  # 左界不足时从头截取（负索引切片会从尾部起算，须钳位）
    est = df.iloc[est_start : est_hi + 1]
    if len(est) < 60:
        return None  # 估计窗有效日 <60 → skip（spec §4.5）
    low_conf, reason = cleaned.low_confidence, cleaned.reason
    if len(est) < 100:
        # 估计窗 60-99 有效日：低置信保留（spec §4.5），叠加清洗阶段已有 reason
        est_reason = f"估计窗有效日{len(est)}<100"
        reason = f"{reason}; {est_reason}" if reason else est_reason
        low_conf = True

    stock_ret = simple_returns(est["close"]).to_numpy()
    bench_ret = simple_returns(est["close_bench"]).to_numpy()
    n = min(len(stock_ret), len(bench_ret))
    stock_ret, bench_ret = stock_ret[-n:], bench_ret[-n:]
    if n < 30:
        return None

    alpha, beta, sigma = fit_market_model(stock_ret, bench_ret)

    # 全序列 AR（用于事件窗）
    full_stock = simple_returns(df["close"]).to_numpy()
    full_bench = simple_returns(df["close_bench"]).to_numpy()
    fn = min(len(full_stock), len(full_bench))
    # 注意 AR 索引相对 t0：simple_returns 第一项为 NaN，故 AR[0] 对应 df 第 2 行
    ar_full = abnormal_returns(full_stock[-fn:], full_bench[-fn:], alpha, beta)
    # t0 在 df 中的位置 → ar_full 对应位置 = t0 - 1（因 pct_change 丢首项）
    ar_t0 = t0 - 1
    if ar_t0 < 0:
        ar_t0 = 0

    def window_car(hi: int) -> float | None:
        start = ar_t0
        end = ar_t0 + hi
        if end >= len(ar_full):
            return None  # 窗口不足不计算（不截断冒充完整窗口）
        return car(ar_full, start, end)

    car_0_1 = window_car(1)
    car_0_5 = window_car(5)
    car_0_20 = window_car(20)
    car_0_60 = window_car(60)
    car_0_90 = window_car(90)
    car_neg1_0 = car(ar_full, max(ar_t0 - 1, 0), ar_t0) if ar_t0 >= 1 else None

    # 事件窗 AR / 成交量（用于持续性与 AAV）
    evt_lo, evt_hi = ar_t0, min(ar_t0 + 90, len(ar_full) - 1)
    ar_evt = ar_full[evt_lo : evt_hi + 1]
    sig_days = sig_ar_streak(ar_evt, sigma)
    # 成交量事件窗
    vol = df["volume"].to_numpy() if "volume" in df.columns else None
    aav = None  # volume 列缺失/估计窗样本不足 → 数据不可得，不冒充"无异常放量"(0)
    if vol is not None:
        vol_est = vol[est_start : est_hi + 1]
        vol_est = vol_est[np.isfinite(vol_est)]
        if len(vol_est) > 1:
            vol_evt = vol[t0 : min(t0 + 91, len(vol))]
            aav = aav_days(vol_evt, vol_est.mean(), vol_est.std(ddof=1))

    # 涨跌停（事件窗 [0,+60] 内逐日）
    up_days = down_days = 0
    closes = df["close"].to_numpy()
    dates = df["date"].to_numpy()
    for k in range(0, min(91, len(df) - t0)):
        idx = t0 + k
        if idx <= 0 or idx >= len(df):
            continue
        prev_c = closes[idx - 1]
        cur_c = closes[idx]
        if prev_c <= 0:
            continue
        pct = (cur_c / prev_c) - 1.0
        d = dates[idx].item() if hasattr(dates[idx], "item") else dates[idx]
        # is_new_first5 恒 False 的前提：clean_quotes 已按上市 >=365 日历天剔除新股，
        # 能走到这里的标的不可能处于上市前 5 个交易日（那期间主板 2023-04-10 起不设限）
        lim = daily_limit_pct(board or Board.MAIN, is_st=is_st, is_new_first5=False, d=d)
        hit = limit_hit(pct, lim, prev_close=prev_c)
        if hit == "up":
            up_days += 1
        elif hit == "down":
            down_days += 1

    # 峰值/谷值 AR / 最大回撤（事件窗）：受损标的看谷值（峰值只报 max 对负向信号无意义）
    peak_ar = float(np.max(ar_evt)) if len(ar_evt) else None
    trough_ar = float(np.min(ar_evt)) if len(ar_evt) else None
    cum = np.cumsum(ar_evt) if len(ar_evt) else np.array([])
    max_dd = float(min(0.0, -(np.maximum.accumulate(cum) - cum).max())) if len(cum) else None

    # 行业基准 CAR（双层：剥离板块 β 后的个股 alpha）。
    # 行业指数覆盖不足的窗口一律 None（不外推、不截断）；估计窗须全覆盖，
    # 否则按位置切片的收益率会与市场模型窗口错位。
    car_ind_0_20 = car_ind_0_60 = car_ind_0_90 = None
    if "close_industry" in df.columns:
        ci = df["close_industry"].to_numpy(dtype=float)
        est_ci = ci[est_start : est_hi + 1]
        if len(est_ci) >= 2 and np.isfinite(est_ci).all():
            ind_ret_est = est_ci[1:] / est_ci[:-1] - 1.0
            if len(ind_ret_est) >= 30 and len(ind_ret_est) >= n and np.isfinite(ind_ret_est).all():
                alpha_i, beta_i, _ = fit_market_model(stock_ret, ind_ret_est[-n:])
                ret_ind_all = ci[1:] / ci[:-1] - 1.0  # 与 full_stock 同基（第 k 项 ↔ df 第 k+1 行）
                # AR = R_stock − (α + β·R_ind)：fit_market_model 第一参数是因变量（个股），
                # 残差须以个股收益为被减项；写反则事件信号以 −β 系数进入（符号反转）
                ar_ind = full_stock - (alpha_i + beta_i * ret_ind_all)

                def _ind_car(hi: int) -> float | None:
                    end = ar_t0 + hi
                    if end >= len(ar_ind):
                        return None  # 窗口不足不计算，与市场 CAR 同口径
                    seg = ar_ind[ar_t0 : end + 1]
                    if not np.isfinite(seg).all():
                        return None  # 行业基准未覆盖完整窗口
                    return float(seg.sum())

                car_ind_0_20 = _ind_car(20)
                car_ind_0_60 = _ind_car(60)
                car_ind_0_90 = _ind_car(90)

    # 事件前 CAR[t-20, t-1]（预期差验证：大→已 pricing in→"利好出尽"风险）
    _pre_start = ar_t0 - 20
    _pre_end = ar_t0 - 1
    car_pre_20 = car(ar_full, _pre_start, _pre_end) if _pre_start >= 0 and _pre_end >= _pre_start else None

    return ImpactMeasurement(
        stock_id=stock_id,
        episode_id=episode_id,
        direction=direction,
        car_0_1=car_0_1,
        car_0_5=car_0_5,
        car_0_20=car_0_20,
        car_0_60=car_0_60,
        car_0_90=car_0_90,
        car_industry_0_20=car_ind_0_20,
        car_industry_0_60=car_ind_0_60,
        car_industry_0_90=car_ind_0_90,
        car_pre_20=car_pre_20,
        car_neg1_0=car_neg1_0,
        peak_ar=peak_ar,
        trough_ar=trough_ar,
        max_drawdown=max_dd,
        limit_up_days=up_days,
        limit_down_days=down_days,
        persistence_ratio_60_5=persistence_ratio(car_0_60, car_0_5),
        sig_ar_days=sig_days,
        aav_days=aav,
        low_confidence=low_conf,
        low_confidence_reason=reason,
        tier=Tier.UNGRADED,  # 由 AttributionFiltering 填
    )
