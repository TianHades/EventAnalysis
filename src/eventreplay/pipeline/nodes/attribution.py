"""AttributionFiltering（两段式，spec §4.3）。

MVP 实现：第一段按经济显著性+direction 划池（direction↔CAR 符号冲突标 direction_conflict）；
第二段池级统计量为"横截面 CAR t 检验"的简化实现（spec §4.3 GRANK 需 SAR 矩阵贯通，
留作后续；此处 t 检验作辅证披露）。
"""

from __future__ import annotations

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


def _pool_stat(cars: list[float]) -> PoolStat:
    n = len(cars)
    if n == 0:
        return PoolStat(n=0, disclosed=False)
    arr = np.array(cars, dtype=float)
    caar = float(arr.mean())
    if n < 5:
        return PoolStat(n=n, caar=caar, disclosed=False)
    t, p = stats.ttest_1samp(arr, 0)
    return PoolStat(
        n=n,
        caar=caar,
        grank_z=float(t),
        grank_p=float(p),
        disclosed=True,
    )


def _car_best(m: ImpactMeasurement) -> float:
    """最强窗口 CAR（+20/+60/+90 中 |CAR| 最大者）——让 late-bloomer（+20 弱、+90 强）不被 +20 阈值漏掉。"""
    cars = [c for c in (m.car_0_20, m.car_0_60, m.car_0_90) if c is not None]
    return max(cars, key=abs) if cars else 0.0


def _stat_car(m: ImpactMeasurement) -> float:
    """池统计量统一取固定窗口 CAR[0,+20]（对三窗取最大会人为膨胀 t 检验显著性）。"""
    return m.car_0_20 if m.car_0_20 is not None else _car_best(m)


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
        # 第一段：经济显著性（+20/+60/+90 任一窗口显著）+ direction 划池
        for m in uniq:
            car = _car_best(m)  # 最强窗口，让 late-bloomer 入池
            d = cand_dir.get(m.stock_id, m.direction)
            if d is None:
                continue
            threshold = economic_threshold(m.board)  # 按标的板块取阈值
            conflict = (d == StockDirection.BENEFICIARY and car < 0) or (
                d == StockDirection.VICTIM and car > 0
            )
            if abs(car) >= threshold:
                pools[d].append(
                    PoolMember(
                        stock_id=m.stock_id,
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
                        chain_node_id=m.chain_node_id,
                        direction=d,
                        tier=Tier.WEAK,
                        episode_id=m.episode_id,
                        low_confidence=m.low_confidence,
                        direction_conflict=conflict,
                        rationale=(m.rationale or "") + " [弱反应]",
                    )
                )

        pool_stats = {
            "beneficiary": _pool_stat([_member_car(m.stock_id, measurements) for m in pools[StockDirection.BENEFICIARY]]),
            "victim": _pool_stat([_member_car(m.stock_id, measurements) for m in pools[StockDirection.VICTIM]]),
        }
        return AttributionResult(
            beneficiary_pool=pools[StockDirection.BENEFICIARY],
            victim_pool=pools[StockDirection.VICTIM],
            pool_stats=pool_stats,
            tier_definitions={"threshold_main": "见 spec §4.3", "pool_stat_method": "MVP简化:横截面CAR[0,+20] t检验(spec GRANK待SAR贯通)"},
            industry_granularity=IndustryGranularity.L1,
        )


def _member_car(stock_id: str, measurements: list[ImpactMeasurement]) -> float:
    for m in measurements:
        if m.stock_id == stock_id:
            return _stat_car(m)
    return 0.0
