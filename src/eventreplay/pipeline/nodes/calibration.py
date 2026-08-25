"""InitialCalibrationNode：盘后校准（实际 [+1]/[+5] CAR vs 预期 → 确认/背离）。"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pandas as pd

from ...adapters.quote_adapter import AkshareQuoteAdapter
from ...adapters.stock_master import StockMaster
from ...measurement.event_study import measure_stock
from ...schema import ForwardPrediction, StockDirection
from ..node import Node


def _parse_range(range_str: str | None) -> tuple[float, float] | None:
    """解析 '10%~30%' → (0.10, 0.30)。"""
    if not range_str:
        return None
    nums = re.findall(r"-?\d+\.?\d*", range_str.replace("%", ""))
    if len(nums) >= 2:
        return float(nums[0]) / 100, float(nums[1]) / 100
    if len(nums) == 1:
        v = float(nums[0]) / 100
        return v, v
    return None


class InitialCalibrationNode(Node):
    """盘后校准：对前瞻预判标的拉实际行情，算 [+5] CAR（[+1] 仅记录），对比预期区间。

    判定只用 car_0_5（[+5] 初始校准窗）；状态语义：
    - pending：数据窗不足（反查失败/行情失败/度量 None/car_0_5 None，如 t0 当天）
    - no_range：纯定性预判（无预期区间）
    - parse_error：区间字符串存在但解析失败
    - confirming / diverging / neutral：真实走平（方向一致未达 50% 达标线）才 neutral
    """

    node_id = "initial_calibration"

    def __init__(self, quote_adapter: AkshareQuoteAdapter, stock_master: StockMaster, storage=None, benchmark: str = "sh000300") -> None:
        super().__init__(storage)
        self.qa = quote_adapter
        self.sm = stock_master
        self.benchmark = benchmark

    def run(self, inputs: dict[str, Any]) -> list[ForwardPrediction]:
        """返回新的 ForwardPrediction 列表（不修改入参；遵循不可变约定）。"""
        event = inputs["event"]
        predictions: list[ForwardPrediction] = inputs["predictions"]
        reaction_date = event.reaction_date

        start = reaction_date - timedelta(days=350)
        end = reaction_date + timedelta(days=30)  # 只需要 [+1]/[+5]，短窗
        try:
            bench = self.qa.index_daily(self.benchmark, start, end)
        except Exception:
            bench = pd.DataFrame()  # 基准拉取失败 → 所有标的 pending（度量不可得）

        out: list[ForwardPrediction] = []
        for pred in predictions:
            resolved = self.sm.resolve(pred.stock_id)
            if resolved is None:
                out.append(pred.model_copy(update={"calibration": "pending"}))
                continue
            try:
                q = self.qa.daily_adjusted(pred.stock_id, start, end)
            except Exception:
                out.append(pred.model_copy(update={"calibration": "pending"}))
                continue
            m = measure_stock(
                stock_id=pred.stock_id,
                episode_id=f"{event.id}-fwd",
                quotes=q,
                bench=bench,
                reaction_date=reaction_date,
                board=resolved.board,
                is_st=resolved.is_st,
                listing_date=resolved.listing_date,
                direction=pred.direction,
            )
            if m is None or m.car_0_5 is None:
                out.append(pred.model_copy(update={"calibration": "pending"}))
                continue

            # 校准判定（用 [+5] 做初始校准）
            actual = m.car_0_5
            if pred.expected_car_0_20_range is None:
                cal = "no_range"
            else:
                rng = _parse_range(pred.expected_car_0_20_range)
                if rng is None:
                    cal = "parse_error"
                else:
                    # 保守边界：区间中幅度最小者；受益/受损同口径（各取该方向 50% 达标线）
                    near = min(abs(x) for x in rng)
                    if pred.direction == StockDirection.BENEFICIARY:
                        if actual > 0:
                            cal = "confirming" if actual >= near * 0.5 else "neutral"
                        else:
                            cal = "diverging"
                    else:  # VICTIM
                        if actual < 0:
                            cal = "confirming" if actual <= -near * 0.5 else "neutral"
                        else:
                            cal = "diverging"

            out.append(
                pred.model_copy(
                    update={
                        "actual_car_0_1": m.car_0_1,
                        "actual_car_0_5": m.car_0_5,
                        "calibration": cal,
                    }
                )
            )
        return out
