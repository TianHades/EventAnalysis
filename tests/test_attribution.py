"""AttributionNode 池统计口径测试：
- car_0_20=None 成员不进池统计（n 反映有效样本，不用三窗最强值膨胀显著性）
- fallback WEAK 成员仅列示，不稀释 caar/t 检验（pool_stats 注明 fallback 数量）
- 基于 car_0_60/90 的判定按 sqrt(窗口/20) 放大经济阈值
- PoolMember 填 stock_name
"""

from datetime import date

from eventreplay.pipeline.nodes.attribution import AttributionNode
from eventreplay.schema import (
    ImpactMeasurement,
    PoolMember,
    StockDirection,
    Tier,
)


def _m(
    stock_id: str,
    car_0_20: float | None,
    *,
    car_0_60: float | None = None,
    car_0_90: float | None = None,
    direction: StockDirection = StockDirection.BENEFICIARY,
    board: str = "主板",
    stock_name: str | None = None,
) -> ImpactMeasurement:
    return ImpactMeasurement(
        stock_id=stock_id,
        episode_id="e-ep0",
        car_0_20=car_0_20,
        car_0_60=car_0_60,
        car_0_90=car_0_90,
        direction=direction,
        board=board,
        stock_name=stock_name or stock_id,
    )


def _run(measurements, candidates=None):
    cands = candidates if candidates is not None else [
        {"stock_id": m.stock_id, "direction": m.direction} for m in measurements
    ]
    return AttributionNode().run({"measurements": measurements, "candidates": cands})


def test_car_0_20_none_members_excluded_from_pool_stat():
    """car_0_20=None 的成员不进池统计（不回退三窗最强值），n 反映有效样本。"""
    ms = [
        _m("600001", 0.20),
        _m("600002", 0.24),
        _m("600003", 0.18),
        _m("600004", 0.22),
        _m("600005", 0.26),
        _m("600006", None, car_0_60=0.90),  # 无 +20 窗口，旧实现回退 0.90 膨胀显著性
    ]
    ar = _run(ms)
    stat = ar.pool_stats["beneficiary"]
    assert stat.n == 5  # 600006 不计入
    assert stat.caar == (0.20 + 0.24 + 0.18 + 0.22 + 0.26) / 5  # 均值不含 0.90


def test_weak_fallback_members_listed_but_not_in_stat():
    """fallback WEAK 成员仅列示（不进 caar/t 检验），pool_stats 注明数量。"""
    # 只有一条弱度量（低于主板 0.10 阈值但高于 0.03 下限）→ 受益池走 fallback
    ms = [_m("600001", 0.05)]
    ar = _run(ms)
    pool = ar.beneficiary_pool
    assert len(pool) == 1
    assert pool[0].tier == Tier.WEAK
    assert pool[0].stock_name == "600001"  # PoolMember 带 stock_name
    stat = ar.pool_stats["beneficiary"]
    assert stat.n == 0              # WEAK 不进统计
    assert stat.caar is None        # 无有效样本 → 不给 caar
    assert stat.fallback_n == 1     # 注明 fallback 数量


def test_tier_threshold_scaled_for_long_window():
    """基于 car_0_60 的判定按 sqrt(60/20)≈1.732 放大经济阈值（主板 0.10 → ~0.173）。"""
    # car_0_20=None、car_0_60=0.18：旧阈值 0.10 → STRONG；放大后 0.18 >= 0.2*0.173? 0.18 < 0.346 → MEDIUM
    ms = [_m("600001", None, car_0_60=0.18)]
    ar = _run(ms)
    assert len(ar.beneficiary_pool) == 1
    m = ar.beneficiary_pool[0]
    # 0.18 >= 0.173（放大阈值）→ 入池；0.18 < 2*0.173 → MEDIUM（旧口径 0.18>=2*0.10 会给 STRONG）
    assert m.tier == Tier.MEDIUM

    # car_0_60=0.12：12% < 放大阈值 0.173 → 不入池；高于 fallback 下限 0.03 → WEAK 列示
    ms2 = [_m("600001", None, car_0_60=0.12)]
    ar2 = _run(ms2)
    assert ar2.beneficiary_pool[0].tier == Tier.WEAK


def test_pool_member_carries_stock_name():
    """正常入池成员的 PoolMember 填 stock_name（度量侧已带）。"""
    ms = [_m("600519", 0.30, stock_name="贵州茅台")]
    ar = _run(ms)
    assert ar.beneficiary_pool[0].stock_name == "贵州茅台"
