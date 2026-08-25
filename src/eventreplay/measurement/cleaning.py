"""数据清洗与低置信标记（spec §4.5）。

规则：上市不足 250 交易日新股；thin trading（换手<0.1% 或 零成交日>10%）；
估计窗<60 有效日；停牌日剔除（volume=0）；事件窗缺失>20%；ST 戴/摘帽；估计窗污染。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from datetime import date


@dataclass(frozen=True)
class CleaningOutcome:
    cleaned: pd.DataFrame  # 去停牌后的 OHLCV
    low_confidence: bool
    reason: str | None


def clean_quotes(
    quotes: pd.DataFrame,
    *,
    listing_date: date | None,
    reaction_date: date,
    estimation_window_days: int,
    min_est_days: int = 60,
    thin_turnover: float = 0.001,
    thin_zero_vol_pct: float = 0.10,
    max_event_missing_pct: float = 0.20,
    min_listing_days: int = 250,
) -> CleaningOutcome | None:
    """清洗个股日线；返回 None 表示该标的应 skip（如新股上市不足）。

    输入 quotes 须含 date/open/high/low/close/volume/turnover 列。
    """
    if quotes is None or quotes.empty:
        return None
    df = quotes.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)

    # 新股剔除（事件日前上市不足 min_listing_days 个交易日，spec §4.5）
    # 按上市日到事件日的"日历天数"判定（250 交易日 ≈ 365 日历日），不依赖行情窗口覆盖。
    if listing_date is not None:
        if listing_date >= reaction_date or (reaction_date - listing_date).days < 365:
            return None

    # close 为 NaN 的行整行剔除（清洗阶段保证无 NaN close 进度量；
    # 否则 pct_change 会产出 NaN/被 pad 伪造成 0% 收益，破坏 stock/bench 对齐）
    if "close" in df.columns:
        df = df.loc[df["close"].notna()].reset_index(drop=True)
        if df.empty:
            return None

    # 事件窗停牌占比（必须在去停牌前算，spec §13 偏离5：高占比→低置信，防 CAR 失真）
    susp_in_event = 0.0
    if "volume" in df.columns and "date" in df.columns:
        evt_raw = df.loc[df["date"] >= reaction_date].head(61)
        if len(evt_raw) > 0:
            susp_in_event = float((evt_raw["volume"] == 0).mean())

    # 零成交占比（估计窗 [t0-120, t0-21]）：必须在去停牌**前**的原始样本上统计——
    # volume==0 行删后再统计恒 ≈0，spec"零成交日>10%→thin trading 低置信"将永远不触发。
    # 注意 t0 在原始 df 与去停牌后 df 上位置不同，需独立定位。
    zero_vol_pct = 0.0
    if "volume" in df.columns and "date" in df.columns and estimation_window_days > 0:
        t0_mask_raw = df["date"] >= reaction_date
        if t0_mask_raw.any():
            t0_raw = t0_mask_raw.idxmax()  # t0 在原始 df 的位置
            est_raw = df.iloc[
                max(0, t0_raw - estimation_window_days) : max(0, t0_raw - 20)
            ]
            if len(est_raw) > 0:
                zero_vol_pct = float((est_raw["volume"] == 0).mean())

    # 停牌日剔除（volume==0）
    if "volume" in df.columns:
        suspended = df["volume"] == 0
        df = df.loc[~suspended].reset_index(drop=True)

    reasons: list[str] = []
    if susp_in_event > 0.20:
        reasons.append(f"事件窗停牌占比{int(susp_in_event * 100)}%")

    # thin trading（估计窗 [t0-120, t0-21]；零成交占比用去停牌前的统计结果）
    if "turnover" in df.columns and estimation_window_days > 0:
        t0_mask = df["date"] >= reaction_date
        if t0_mask.any():
            t0_pos = t0_mask.idxmax()  # t0 在去停牌后 df 的位置
            est_start = max(0, t0_pos - estimation_window_days)
            est_end = max(0, t0_pos - 20)
            est_sample = df.iloc[est_start:est_end] if est_end > est_start else df.iloc[:0]
            if not est_sample.empty:
                avg_turnover = est_sample["turnover"].mean()
                if (avg_turnover is not None and avg_turnover < thin_turnover) or zero_vol_pct > thin_zero_vol_pct:
                    reasons.append("thin trading")

    # 估计窗不足
    if len(df) < min_est_days:
        reasons.append(f"有效交易日不足{min_est_days}")

    low_conf = bool(reasons)
    return CleaningOutcome(
        cleaned=df,
        low_confidence=low_conf,
        reason="; ".join(reasons) if reasons else None,
    )
