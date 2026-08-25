"""AttributionFiltering（两段式，spec §4.3）。

MVP 实现：第一段按经济显著性+direction 划池（direction↔CAR 符号冲突标 direction_conflict）；
第二段池级统计量为"横截面 CAR t 检验"的简化实现（spec §4.3 GRANK 需 SAR 矩阵贯通，
留作后续；此处 t 检验作辅证披露）。

口径：
- 池统计只用固定窗口 CAR[0,+20]——car_0_20 缺失的成员不进统计（回退三窗最强值会
  人为膨胀 t 检验显著性），n 反映有效样本。
- fallback WEAK 成员仅列示，不进 caar/t 检验（pool_stats.fallback_n 注明数量）。
- 划池显著性允许三窗最强（让 late-bloomer 入池），但 +60/+90 窗口的经济阈值按
  sqrt(窗口/20) 放大（同等幅度在长窗口更容易达到，须提高门槛）。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats

from ...measurement.significance import economic_threshold
from ...schema import (
    AttributionResult,
    ImpactMeasurement,
    IndustryGranularity,
    PoolMember,
    PoolStat,
    StockDirection,
    Tier,
)
from ..node import Node

_FALLBACK_FLOOR = {"主板": 0.03, "创业板": 0.06, "科创板": 0.06, "北交所": 0.09, "港股主板": 0.05}


def _tier(car_abs: float, threshold: float, low_conf: bool) -> Tier:
    if low_conf:
        return Tier.LOW_CONFIDENCE
    if car_abs >= 2 * threshold:
        return Tier.STRONG
    return Tier.MEDIUM


def _pool_stat(cars: list[float | None], *, fallback_n: int = 0) -> PoolStat:
    """cars 中 None（car_0_20 缺失）的成员被过滤，n 反映有效样本。"""
    vals = [c for c in cars if c is not None]
    n = len(vals)
    if n == 0:
        return PoolStat(n=0, disclosed=False, fallback_n=fallback_n)
    arr = np.array(vals, dtype=float)
    caar = float(arr.mean())
    if n < 5:
        return PoolStat(n=n, caar=caar, disclosed=False, fallback_n=fallback_n)
    t, p = stats.ttest_1samp(arr, 0)
    return PoolStat(
        n=n,
        caar=caar,
        grank_z=float(t),
        grank_p=float(p),
        disclosed=True,
        fallback_n=fallback_n,
    )


def _car_best_window(m: ImpactMeasurement) -> tuple[float, int]:
    """最强窗口 CAR 及其窗口天数（+20/+60/+90 中 |CAR| 最大者）——让 late-bloomer（+20 弱、+90 强）不被 +20 阈值漏掉。"""
    best: tuple[float, int] | None = None
    for car, window in ((m.car_0_20, 20), (m.car_0_60, 60), (m.car_0_90, 90)):
        if car is None:
            continue
        if best is None or abs(car) > abs(best[0]):
            best = (car, window)
    return best if best is not None else (0.0, 20)


def _car_best(m: ImpactMeasurement) -> float:
    return _car_best_window(m)[0]


def _window_scaled_threshold(threshold: float, window: int) -> float:
    """长窗口经济阈值按 sqrt(窗口/20) 放大（+60 → ×1.732，+90 → ×2.121）。"""
    return threshold * math.sqrt(window / 20)


def _stat_car(m: ImpactMeasurement) -> float | None:
    """池统计量只用固定窗口 CAR[0,+20]；缺失即 None（不回退三窗最强值，防显著性膨胀）。"""
    return m.car_0_20


class AttributionNode(Node):
    node_id = "attribution_filtering"
    output_type = AttributionResult  # SQLite 缓存重建

    def run(self, inputs: dict[str, Any]) -> AttributionResult:
        measurements: list[ImpactMeasurement] = inputs["measurements"]
        candidates = inputs["candidates"]
        cand_dir = {c["stock_id"]: c["direction"] for c in candidates}

        # 去重（多次 replay 可能写入重复度量行）
        seen: set[str] = set()
        uniq: list[ImpactMeasurement] = []
        for m in measurements:
            if m.stock_id not in seen:
                seen.add(m.stock_id)
                uniq.append(m)

        pools: dict[StockDirection, list[PoolMember]] = {
            StockDirection.BENEFICIARY: [],
            StockDirection.VICTIM: [],
        }
        # 第一段：经济显著性（+20/+60/+90 任一窗口显著，长窗口阈值放大）+ direction 划池
        for m in uniq:
            car, window = _car_best_window(m)  # 最强窗口，让 late-bloomer 入池
            d = cand_dir.get(m.stock_id, m.direction)
            if d is None:
                continue
            threshold = _window_scaled_threshold(economic_threshold(m.board), window)
            conflict = (d == StockDirection.BENEFICIARY and car < 0) or (
                d == StockDirection.VICTIM and car > 0
            )
            if abs(car) >= threshold:
                pools[d].append(
                    PoolMember(
                        stock_id=m.stock_id,
                        stock_name=m.stock_name,
                        chain_node_id=m.chain_node_id,
                        direction=d,
                        tier=_tier(abs(car), threshold, m.low_confidence),
                        episode_id=m.episode_id,
                        low_confidence=m.low_confidence,
                        direction_conflict=conflict,
                        rationale=m.rationale,
                    )
                )

        # fallback：某方向空池 → 取 direction 一致的 top-5（带经济下限）
        by_dir: dict[StockDirection, list[tuple[float, ImpactMeasurement]]] = {
            StockDirection.BENEFICIARY: [],
            StockDirection.VICTIM: [],
        }
        for m in uniq:
            d = cand_dir.get(m.stock_id, m.direction)
            if d:
                by_dir[d].append((abs(_car_best(m)), m))
        for d, members in pools.items():
            if members:
                continue
            ranked = sorted(by_dir.get(d, []), key=lambda x: x[0], reverse=True)
            kept = [(a, m) for a, m in ranked if a >= _FALLBACK_FLOOR.get(m.board, 0.03)][:5]
            for a, m in kept:
                car_val = _car_best(m)
                conflict = (d == StockDirection.BENEFICIARY and car_val < 0) or (
                    d == StockDirection.VICTIM and car_val > 0
                )
                members.append(
                    PoolMember(
                        stock_id=m.stock_id,
                        stock_name=m.stock_name,
                        chain_node_id=m.chain_node_id,
                        direction=d,
                        tier=Tier.WEAK,
                        episode_id=m.episode_id,
                        low_confidence=m.low_confidence,
                        direction_conflict=conflict,
                        rationale=(m.rationale or "") + " [弱反应]",
                    )
                )

        # 池统计：只用 car_0_20（缺失成员剔除）；WEAK fallback 成员仅列示不计入
        def _stat_for(members: list[PoolMember]) -> PoolStat:
            fallback_n = sum(1 for x in members if x.tier == Tier.WEAK)
            cars = [
                _member_car(x.stock_id, measurements)
                for x in members
                if x.tier != Tier.WEAK
            ]
            return _pool_stat(cars, fallback_n=fallback_n)

        pool_stats = {
            "beneficiary": _stat_for(pools[StockDirection.BENEFICIARY]),
            "victim": _stat_for(pools[StockDirection.VICTIM]),
        }
        return AttributionResult(
            beneficiary_pool=pools[StockDirection.BENEFICIARY],
            victim_pool=pools[StockDirection.VICTIM],
            pool_stats=pool_stats,
            tier_definitions={"threshold_main": "见 spec §4.3", "pool_stat_method": "MVP简化:横截面CAR[0,+20] t检验(spec GRANK待SAR贯通)"},
            industry_granularity=IndustryGranularity.L1,
        )


def _member_car(stock_id: str, measurements: list[ImpactMeasurement]) -> float | None:
    """成员的 CAR[0,+20]（无则 None，由 _pool_stat 过滤）。"""
    for m in measurements:
        if m.stock_id == stock_id:
            return _stat_car(m)
    return None
