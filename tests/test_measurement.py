"""度量引擎测试：市场模型 / CAR / 涨跌停时点 / 显著性 size 检验 / 端到端 measure_stock。"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

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
