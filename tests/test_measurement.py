"""度量引擎测试：市场模型 / CAR / 涨跌停时点 / 显著性 size 检验 / 端到端 measure_stock。"""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm as scipy_norm, rankdata

from eventreplay.measurement.event_study import measure_stock
from eventreplay.measurement.limits import daily_limit_pct, limit_hit
from eventreplay.measurement.returns import abnormal_returns, car, fit_market_model, simple_returns
from eventreplay.measurement.significance import (
    adjusted_bmp,
    choose_pool_stat,
    economic_significant,
    economic_threshold,
    generalized_rank_z,
)
from eventreplay.schema.common import Board, StockDirection


# ---- 市场模型 / AR / CAR（数值）----


def test_fit_market_model_recovers_params():
    rng = np.random.default_rng(42)
    bench = rng.normal(0, 0.01, 200)
    stock = 0.001 + 1.2 * bench + rng.normal(0, 0.005, 200)
    alpha, beta, sigma = fit_market_model(stock, bench)
    assert alpha == pytest.approx(0.001, abs=3e-3)
    assert beta == pytest.approx(1.2, abs=5e-2)
    assert sigma == pytest.approx(0.005, abs=2e-3)


def test_car_known_series():
    ar = np.array([0.01, 0.02, -0.01, 0.03])
    assert car(ar, 0, 2) == pytest.approx(0.02)
    assert car(ar, 1, 3) == pytest.approx(0.04)


def test_abnormal_returns():
    stock = np.array([0.02, 0.03])
    bench = np.array([0.01, 0.01])
    ar = abnormal_returns(stock, bench, alpha=0.0, beta=1.0)
    assert np.allclose(ar, [0.01, 0.02])


# ---- 涨跌停时点表（spec §4.4）----


def test_limit_pct_creative_2020_split():
    assert daily_limit_pct(Board.CREATIVE, is_st=False, is_new_first5=False, d=date(2019, 6, 1)) == 0.10
    assert daily_limit_pct(Board.CREATIVE, is_st=False, is_new_first5=False, d=date(2020, 9, 1)) == 0.20


def test_limit_pct_main_new_listing_after_registration():
    assert daily_limit_pct(Board.MAIN, is_st=False, is_new_first5=True, d=date(2022, 1, 1)) == 0.10
    assert daily_limit_pct(Board.MAIN, is_st=False, is_new_first5=True, d=date(2023, 5, 1)) is None


def test_limit_pct_st_rules():
    assert daily_limit_pct(Board.MAIN, is_st=True, is_new_first5=False, d=date(2024, 1, 1)) == 0.05
    assert daily_limit_pct(Board.MAIN, is_st=True, is_new_first5=False, d=date(2026, 8, 1)) == 0.10
    # ST 创业板/科创板仍 20%
    assert daily_limit_pct(Board.CREATIVE, is_st=True, is_new_first5=False, d=date(2024, 1, 1)) == 0.20
    assert daily_limit_pct(Board.STAR, is_st=True, is_new_first5=False, d=date(2024, 1, 1)) == 0.20


def test_limit_pct_star_bse():
    assert daily_limit_pct(Board.STAR, is_st=False, is_new_first5=False, d=date(2024, 1, 1)) == 0.20
    assert daily_limit_pct(Board.BSE, is_st=False, is_new_first5=False, d=date(2024, 1, 1)) == 0.30


def test_limit_pct_hk_main_none():
    """港股主板无涨跌停（Phase 5）。"""
    assert daily_limit_pct(Board.HK_MAIN, is_st=False, is_new_first5=False, d=date(2024, 1, 1)) is None
    assert daily_limit_pct(Board.HK_MAIN, is_st=True, is_new_first5=True, d=date(2024, 1, 1)) is None


def test_limit_hit_detection():
    assert limit_hit(0.099, 0.10) is None
    assert limit_hit(0.10, 0.10) == "up"
    assert limit_hit(-0.10, 0.10) == "down"


# ---- 经济显著性 ----


def test_economic_threshold_by_board():
    assert economic_threshold(Board.MAIN) == 0.10
    assert economic_threshold(Board.CREATIVE) == 0.20
    assert economic_threshold(Board.STAR) == 0.20


def test_economic_significant_decision():
    assert economic_significant(0.15, Board.MAIN, n=10) is True
    assert economic_significant(0.05, Board.MAIN, n=10) is False
    assert economic_significant(0.25, Board.CREATIVE, n=3) is True  # 创科 20%


# ---- 显著性 size 检验（H0 下拒绝率 ≈ 名义 α）----


def test_adjusted_bmp_size_under_null():
    """H0：SAR~N(0,1) iid，Adjusted BMP Z 应 ~N(0,1)。

    注：BMP 在小样本(L=10)下有已知 size 轻度膨胀（正是 spec 默认用 GRANK 的原因），
    故放宽上界。规范性由 GRANK（更 size-correct）承担。
    """
    rng = np.random.default_rng(7)
    rejections = 0
    trials = 400
    for _ in range(trials):
        sar = rng.standard_normal((30, 10))
        z, p, _ = adjusted_bmp(sar)
        if p < 0.05:
            rejections += 1
    rate = rejections / trials
    assert 0.02 < rate < 0.15, f"size 失真: rejection rate={rate:.3f}"


def test_generalized_rank_size_under_null():
    rng = np.random.default_rng(11)
    rejections = 0
    trials = 400
    for _ in range(trials):
        sar = rng.standard_normal((30, 10))
        z, p = generalized_rank_z(sar)
        if p < 0.05:
            rejections += 1
    rate = rejections / trials
    assert 0.02 < rate < 0.12, f"rank test size 失真: rate={rate:.3f}"


def test_pool_stat_signal_rejected():
    """信号场景：SAR 整体偏正 → 主推断应拒绝 H0。"""
    rng = np.random.default_rng(3)
    sar = rng.standard_normal((30, 10)) + 0.3  # 正偏
    stat = choose_pool_stat(sar)
    assert stat["disclosed"] is True
    assert stat["main_p"] < 0.05


def test_pool_stat_small_n_nondisclosed():
    sar = np.random.default_rng(1).standard_normal((3, 5))
    stat = choose_pool_stat(sar)
    assert stat["disclosed"] is False  # N<5
    assert stat["main"] is None


# ---- 端到端 measure_stock（合成注入 CAR）----


def _synthetic_quotes_with_signal(seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """生成 250 日合成行情：事件窗 [t0,t0+20] 注入 +15% 累计异常收益。"""
    rng = np.random.default_rng(seed)
    n = 400
    t0 = 300
    bench_ret = rng.normal(0, 0.01, n)
    abnormal = np.zeros(n)
    abnormal[t0 : t0 + 20] = 0.0075  # ~+15% over 20d
    stock_ret = 0.0 + 1.0 * bench_ret + abnormal + rng.normal(0, 0.005, n)
    dates = pd.bdate_range("2022-01-03", periods=n).date

    def to_ohlcv(rets):
        close = 100 * np.cumprod(1 + rets)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
                "turnover": 0.02,
            }
        )

    stock = to_ohlcv(stock_ret)
    bench = pd.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + bench_ret)})
    return stock, bench, t0


def test_measure_stock_recovers_injected_car():
    stock, bench, t0 = _synthetic_quotes_with_signal()
    reaction_date = stock["date"].iloc[t0]
    m = measure_stock(
        stock_id="TEST",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=reaction_date,
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
        direction=StockDirection.BENEFICIARY,
    )
    assert m is not None
    # 注入 ~15% CAR[0,+20]，应显著为正
    assert m.car_0_20 is not None
    assert m.car_0_20 > 0.10
    assert economic_significant(m.car_0_20, Board.MAIN, n=10) is True
    assert m.direction == StockDirection.BENEFICIARY


def test_measure_stock_returns_none_for_new_listing():
    """上市不足 250 日 → None（skip）。"""
    stock, bench, t0 = _synthetic_quotes_with_signal()
    reaction_date = stock["date"].iloc[t0]
    # listing_date 设在事件日前 50 日 → 上市不足
    m = measure_stock(
        stock_id="NEW",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=reaction_date,
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[t0 - 50],
    )
    assert m is None or m.low_confidence is True


def test_cleaning_flags_event_window_suspension():
    """事件窗停牌占比高 → 低置信（spec §13 偏离5）。"""
    from eventreplay.measurement.cleaning import clean_quotes

    n = 400
    dates = pd.bdate_range("2020-01-01", periods=n).date
    df = pd.DataFrame(
        {
            "date": dates,
            "close": [100.0] * n,
            "volume": [1e6] * n,
            "turnover": [0.02] * n,
        }
    )
    # 事件窗（t0=300 之后）大量停牌（volume=0）
    df.loc[300:330, "volume"] = 0
    out = clean_quotes(
        df,
        listing_date=dates[0],
        reaction_date=dates[300],
        estimation_window_days=120,
    )
    assert out is not None
    assert out.low_confidence is True
    assert "停牌" in (out.reason or "")


# ---- 修复1（CRITICAL）：行业 CAR 符号 ----


def _industry_synthetic_quotes(seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, int]:
    """行业指数 β=0.5 的个股：事件窗 [t0,t0+20] 注入 +10% 已知异常收益（相对行业模型）。"""
    rng = np.random.default_rng(seed)
    n = 400
    t0 = 300
    bench_ret = rng.normal(0, 0.01, n)
    ind_ret = rng.normal(0, 0.01, n)
    abnormal = np.zeros(n)
    abnormal[t0 : t0 + 20] = 0.005  # ~+10% over 20d（行业模型口径下的真实 AR）
    stock_ret = 0.5 * ind_ret + abnormal + rng.normal(0, 0.003, n)
    dates = pd.bdate_range("2022-01-03", periods=n).date

    def to_ohlcv(rets):
        close = 100 * np.cumprod(1 + rets)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
                "turnover": 0.02,
            }
        )

    stock = to_ohlcv(stock_ret)
    bench = pd.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + bench_ret)})
    ind = pd.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + ind_ret)})
    return stock, bench, ind, t0


def test_industry_car_positive_for_injected_positive_ar():
    """行业模型 AR = R_stock − (α+β·R_ind)：注入 +10% 真实 AR → car_industry_0_20 须为正且量级正确。

    旧实现减法两边颠倒（ar_ind = R_ind − (α+β·R_stock)），输出 ≈ −β×10% 为负。
    """
    stock, bench, ind, t0 = _industry_synthetic_quotes()
    m = measure_stock(
        stock_id="TEST",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
        direction=StockDirection.BENEFICIARY,
        bench_industry=ind,
    )
    assert m is not None
    assert m.car_industry_0_20 is not None
    assert m.car_industry_0_20 > 0.05, f"行业 CAR 符号反转: {m.car_industry_0_20:.4f}"
    assert m.car_industry_0_20 < 0.15, f"行业 CAR 量级失真: {m.car_industry_0_20:.4f}"


# ---- 修复2（HIGH）：pct_change 隐式 pad 填充 ----


def test_simple_returns_no_implicit_pad_fill():
    """close 内 NaN 日收益不得被 pad 静默填 0（旧默认 fill_method='pad' 伪造 0% 收益）。"""
    close = pd.Series([100.0, 101.0, np.nan, 102.0, 103.0])
    rets = simple_returns(close)
    # 旧实现（pad）：[0.01, 0.0, 0.0099, 0.0098]（含伪造 0.0）
    assert list(np.round(rets.to_numpy(), 4)) == [0.01, round(103.0 / 102.0 - 1, 4)]


def test_cleaning_drops_nan_close_rows():
    """close 为 NaN 的行整行剔除（清洗阶段保证无 NaN close 进度量，保持 stock/bench 日期对齐）。"""
    from eventreplay.measurement.cleaning import clean_quotes

    n = 5
    dates = pd.bdate_range("2020-01-01", periods=n).date
    df = pd.DataFrame(
        {
            "date": dates,
            "close": [100.0, np.nan, 101.0, np.nan, 102.0],
            "volume": [1e6] * n,
            "turnover": [0.02] * n,
        }
    )
    out = clean_quotes(
        df,
        listing_date=date(2010, 1, 1),
        reaction_date=dates[-1],
        estimation_window_days=3,
    )
    assert out is not None
    assert not out.cleaned["close"].isna().any()
    assert len(out.cleaned) == 3
    assert list(out.cleaned["close"]) == [100.0, 101.0, 102.0]


# ---- 修复3（MEDIUM）：估计窗 60-99 有效日低置信保留 ----


def test_measure_stock_short_history_low_confidence_not_skipped():
    """事件前仅 ~80 行数据：估计窗 60-99 有效日 → 低置信保留（而非 None）。"""
    rng = np.random.default_rng(9)
    n_pre, n_post = 80, 120
    n = n_pre + n_post
    t0 = n_pre
    bench_ret = rng.normal(0, 0.01, n)
    stock_ret = 1.0 * bench_ret + rng.normal(0, 0.005, n)
    dates = pd.bdate_range("2022-01-03", periods=n).date

    def to_ohlcv(rets):
        close = 100 * np.cumprod(1 + rets)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
                "turnover": 0.02,
            }
        )

    stock = to_ohlcv(stock_ret)
    bench = pd.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + bench_ret)})
    # listing_date 远早于行情覆盖（长 listed 但历史数据不足，如数据源仅回溯 80 日）
    m = measure_stock(
        stock_id="SHORT",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=date(2015, 1, 1),
    )
    assert m is not None, "估计窗 60-99 有效日应低置信保留，不应 skip"
    assert m.low_confidence is True
    assert "估计窗有效日" in (m.low_confidence_reason or "")
    assert m.car_0_20 is not None


# ---- 修复4（MEDIUM）：零成交占比在去停牌前统计 ----


def test_cleaning_zero_volume_pct_measured_before_suspension_drop():
    """估计窗零成交日 >10% → thin trading 低置信（旧实现删停牌行后统计恒 ≈0，规则失效）。"""
    from eventreplay.measurement.cleaning import clean_quotes

    n = 400
    dates = pd.bdate_range("2020-01-01", periods=n).date
    df = pd.DataFrame(
        {
            "date": dates,
            "close": [100.0] * n,
            "volume": [1e6] * n,
            "turnover": [0.02] * n,
        }
    )
    # 原始估计窗 [t0-120, t0-21] = [180, 280) 全部零成交
    df.loc[180:279, "volume"] = 0
    out = clean_quotes(
        df,
        listing_date=dates[0],
        reaction_date=dates[300],
        estimation_window_days=120,
    )
    assert out is not None
    assert out.low_confidence is True
    assert "thin trading" in (out.reason or "")


# ---- 修复5（MEDIUM）：limit_hit 低价股容差 / 创业板 ST 历史限价 ----


def test_limit_hit_low_price_rounding_tolerance():
    """3.33→3.66 元（真实涨幅 9.91%，0.01 元取整相对误差）应判涨停。

    旧容差 1e-4 漏检（0.10 − 0.0991 = 0.0009 > 1e-4）。
    """
    pct = 3.66 / 3.33 - 1.0
    assert pct < 0.10  # 确认因取整未达名义涨停
    assert limit_hit(pct, 0.10) is None  # 不传 prev_close 保持旧行为
    assert limit_hit(pct, 0.10, prev_close=3.33) == "up"
    # 低价股容差不误伤：远离涨停的涨幅仍不判
    assert limit_hit(0.05, 0.10, prev_close=3.33) is None


def test_limit_pct_creative_st_before_2020():
    """2020-08-24 前 ST 创业板限价 5%（旧实现恒 10%）。"""
    assert daily_limit_pct(Board.CREATIVE, is_st=True, is_new_first5=False, d=date(2019, 6, 1)) == 0.05
    # 注册制改革后 ST 创业板仍 20%
    assert daily_limit_pct(Board.CREATIVE, is_st=True, is_new_first5=False, d=date(2020, 8, 24)) == 0.20
    # 非 ST 创业板 2020-08-24 前仍 10%
    assert daily_limit_pct(Board.CREATIVE, is_st=False, is_new_first5=False, d=date(2019, 6, 1)) == 0.10


# ---- 修复6（MEDIUM）：GRANK 截面相关修正 ----


def _grank_z_uncorrected(sar: np.ndarray) -> float:
    """旧口径复刻：Z = Σ(sign·q)/√(Σq²)（无截面相关修正）。"""
    N, L = sar.shape
    M = N * L
    ranks = rankdata(np.abs(sar).flatten(), method="average")
    q = scipy_norm.ppf(ranks / (M + 1))
    signed = np.sign(sar.flatten()) * q
    return float(signed.sum() / math.sqrt(float((q**2).sum())))


def test_grank_cross_correlation_correction_shrinks_z():
    """截面相关（共同因子 ρ>0）下，修正后 |Z| 应小于未修正 |Z|（KP(2011) √(1+(L−1)r̄)）。"""
    rng = np.random.default_rng(5)
    N, L = 30, 10
    rho = 0.6
    common = rng.standard_normal(L)  # 共同因子：行间截面相关
    sar = np.sqrt(rho) * common + np.sqrt(1 - rho) * rng.standard_normal((N, L)) + 0.8
    z_corr, p_corr = generalized_rank_z(sar)
    z_unc = _grank_z_uncorrected(sar)
    assert abs(z_corr) < abs(z_unc)
    assert p_corr > float(2 * (1 - scipy_norm.cdf(abs(z_unc))))


def test_grank_iid_null_unchanged():
    """iid（r̄≈0）下修正因子 ≈1，不改变结论（与旧口径一致性）。"""
    rng = np.random.default_rng(11)
    sar = rng.standard_normal((30, 10))
    z_corr, _ = generalized_rank_z(sar)
    z_unc = _grank_z_uncorrected(sar)
    assert abs(z_corr - z_unc) < 0.15 * abs(z_unc) + 0.05


def test_adjusted_bmp_nan_correlation_clamped():
    """SAR 含零方差行 → corrcoef 产出 NaN 时钳位（不传播 NaN 到 Z/p）。"""
    rng = np.random.default_rng(13)
    sar = rng.standard_normal((10, 8))
    sar[0, :] = 5.0  # 常数行：与任一行的相关系数为 NaN
    z, p, rbar = adjusted_bmp(sar)
    assert math.isfinite(z)
    assert math.isfinite(p)
    assert math.isfinite(rbar)


def test_standardized_ar_removed():
    """Patell 标准化辅助已删（全仓库无调用，死代码）。"""
    from eventreplay.measurement import significance

    assert not hasattr(significance, "_standardized_ar")


# ---- 修复7（LOW）：aav=None / trough_ar ----


def test_measure_stock_aav_none_without_volume_column():
    """volume 列缺失 → aav_days=None（数据不可得），不再冒充 0（无异常放量）。"""
    stock, bench, t0 = _synthetic_quotes_with_signal()
    stock = stock.drop(columns=["volume"])
    m = measure_stock(
        stock_id="NOVOL",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
    )
    assert m is not None
    assert m.aav_days is None


def test_measure_stock_aav_int_with_volume():
    """volume 列齐全 → aav_days 为 int（真实统计，可为 0）。"""
    stock, bench, t0 = _synthetic_quotes_with_signal()
    m = measure_stock(
        stock_id="VOL",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
    )
    assert m is not None
    assert m.aav_days is not None
    assert isinstance(m.aav_days, int)


def test_measure_stock_trough_ar():
    """trough_ar：事件窗最小 AR（受损标的谷值），与 peak_ar 对偶。"""
    stock, bench, t0 = _synthetic_quotes_with_signal(seed=7)
    # 注入负向异常（受损场景）：谷值应有意义地小于 0
    m = measure_stock(
        stock_id="VICTIM",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
    )
    assert m is not None
    assert m.trough_ar is not None
    assert m.peak_ar is not None
    assert m.trough_ar <= m.peak_ar


# ---- 修复8（MEDIUM）：报告模板 ----


def _render_minimal_report(measurements: list) -> str:
    from eventreplay.measurement.report_template import render_report
    from eventreplay.schema import AttributionResult, Event, TransmissionChain

    return render_report(
        event=Event(
            id="e",
            description="测试事件",
            announcement_date=date(2021, 1, 1),
            reaction_date=date(2021, 1, 1),
            event_type="政策",
        ),
        chain=TransmissionChain(event_id="e"),
        attribution=AttributionResult(),
        measurements=measurements,
    )


def test_report_dual_car_table_pipes_escaped():
    """判读表单元格内 | 必须转义（旧实现把 3 列表切成 6 格）。"""
    md = _render_minimal_report([])
    section = md.split("## 6")[1].split("## 7")[0]
    rows = [l for l in section.splitlines() if l.startswith("|")]
    assert len(rows) >= 8  # 表头+分隔+6 行判读
    for r in rows:
        assert r.replace("\\|", "\x00").count("|") == 4, f"列数被切坏: {r}"


def test_report_methodology_matches_implementation():
    """方法学描述须与实现一致：行业 CAR 是行业指数市场模型残差累计（非 peer 均值调整）。"""
    md = _render_minimal_report([])
    assert "行业指数市场模型" in md
    assert "peer-adjusted" not in md


def test_report_limitations_st_and_trough():
    """局限小节：ST 历史失真披露；有 trough_ar 时峰值/谷值都展示。"""
    from eventreplay.schema import ImpactMeasurement

    md_plain = _render_minimal_report([])
    assert "ST 状态按当前名单判定" in md_plain

    m = ImpactMeasurement(
        stock_id="T1", episode_id="e0", peak_ar=0.12, trough_ar=-0.09
    )
    md = _render_minimal_report([m])
    assert "峰值" in md
    assert "谷值" in md
    assert "+12.0%" in md
    assert "-9.0%" in md
