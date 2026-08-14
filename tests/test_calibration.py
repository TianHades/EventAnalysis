"""InitialCalibrationNode 测试：区间解析 + confirming/diverging/neutral 判定 + 不可变。

验收（计划 P2）：合成行情 → 断言校准判定。
此处用 monkeypatch 替换 measure_stock 以隔离判定逻辑（car_0_5 可控）。
"""

from datetime import date

import pandas as pd

from eventreplay.adapters.stock_master import ResolvedStock
from eventreplay.pipeline.nodes.calibration import InitialCalibrationNode, _parse_range
from eventreplay.schema import ForwardPrediction, ImpactMeasurement, StockDirection
from eventreplay.schema.event import Event


# ---- _parse_range ----


def test_parse_range_two_numbers():
    assert _parse_range("10%~30%") == (0.10, 0.30)


def test_parse_range_negative():
    assert _parse_range("-5%~-15%") == (-0.05, -0.15)


def test_parse_range_single_number():
    assert _parse_range("20%") == (0.20, 0.20)


def test_parse_range_invalid():
    assert _parse_range(None) is None
    assert _parse_range("") is None
    assert _parse_range("abc") is None


# ---- run() 判定逻辑 ----


class FakeStockMaster:
    def resolve(self, identifier: str):
        if identifier == "688981":
            return ResolvedStock(
                code="688981", name="中芯国际", market="沪", board="科创板",
                listing_date=date(2020, 7, 16),
            )
        if identifier == "000725":
            return ResolvedStock(
                code="000725", name="京东方A", market="深", board="主板",
                listing_date=date(2000, 1, 1),
            )
        return None


class FakeQuoteAdapter:
    def daily_adjusted(self, code, start, end, adjust="hfq"):
        return pd.DataFrame({"date": [date(2026, 7, 15)], "close": [10.0]})

    def index_daily(self, symbol, start, end):
        return pd.DataFrame({"date": [date(2026, 7, 15)], "close": [3000.0]})

    def trade_dates(self):
        return [date(2026, 7, 15)]


def _event() -> Event:
    return Event(
        id="e", description="芯片管制",
        announcement_date=date(2026, 7, 15), reaction_date=date(2026, 7, 15),
        event_type="地缘",
    )


def _pred(stock_id: str, direction: StockDirection, expected: str | None = "10%~30%") -> ForwardPrediction:
    return ForwardPrediction(
        stock_id=stock_id, stock_name=stock_id, direction=direction,
        expected_car_0_20_range=expected, rationale="x",
    )


def _make_node(monkeypatch, *, car_0_1=0.0, car_0_5=0.0, m_none=False):
    """构造节点并 patch measure_stock 返回受控 CAR。"""
    node = InitialCalibrationNode(FakeQuoteAdapter(), FakeStockMaster(), storage=None)
    if m_none:
        monkeypatch.setattr(
            "eventreplay.pipeline.nodes.calibration.measure_stock", lambda **kw: None
        )
    else:
        def _fake_measure(**kw):
            return ImpactMeasurement(
                stock_id=kw["stock_id"], episode_id=kw["episode_id"],
                car_0_1=car_0_1, car_0_5=car_0_5,
            )
        monkeypatch.setattr(
            "eventreplay.pipeline.nodes.calibration.measure_stock", _fake_measure
        )
    return node


# ---- 受益方向 ----


def test_beneficiary_confirming(monkeypatch):
    """预期 10%~30%，实际 +15% → confirming（>= 10%*0.5=5%）。"""
    node = _make_node(monkeypatch, car_0_1=0.05, car_0_5=0.15)
    out = node.run({"event": _event(), "predictions": [_pred("688981", StockDirection.BENEFICIARY, "10%~30%")]})
    assert out[0].calibration == "confirming"
    assert out[0].actual_car_0_5 == 0.15
    assert out[0].actual_car_0_1 == 0.05


def test_beneficiary_neutral_small_gain(monkeypatch):
    """受益方向，实际 +2%（>0 但 < 5% 阈值）→ neutral。"""
    node = _make_node(monkeypatch, car_0_1=0.005, car_0_5=0.02)
    out = node.run({"event": _event(), "predictions": [_pred("688981", StockDirection.BENEFICIARY, "10%~30%")]})
    assert out[0].calibration == "neutral"


def test_beneficiary_diverging(monkeypatch):
    """受益方向但实际 -10% → diverging。"""
    node = _make_node(monkeypatch, car_0_1=-0.02, car_0_5=-0.10)
    out = node.run({"event": _event(), "predictions": [_pred("688981", StockDirection.BENEFICIARY, "10%~30%")]})
    assert out[0].calibration == "diverging"


# ---- 受损方向 ----


def test_victim_confirming(monkeypatch):
    """受损方向，预期 -5%~-15%，实际 -10% → confirming（<= -2.5%）。"""
    node = _make_node(monkeypatch, car_0_1=-0.02, car_0_5=-0.10)
    out = node.run({"event": _event(), "predictions": [_pred("000725", StockDirection.VICTIM, "-5%~-15%")]})
    assert out[0].calibration == "confirming"


def test_victim_diverging(monkeypatch):
    """受损方向但实际 +8% → diverging。"""
    node = _make_node(monkeypatch, car_0_1=0.02, car_0_5=0.08)
    out = node.run({"event": _event(), "predictions": [_pred("000725", StockDirection.VICTIM, "-5%~-15%")]})
    assert out[0].calibration == "diverging"


def test_victim_neutral_small_loss(monkeypatch):
    """受损方向，实际 -1%（<0 但 > -2.5% 阈值）→ neutral。"""
    node = _make_node(monkeypatch, car_0_1=-0.002, car_0_5=-0.01)
    out = node.run({"event": _event(), "predictions": [_pred("000725", StockDirection.VICTIM, "-5%~-15%")]})
    assert out[0].calibration == "neutral"


# ---- 边界 ----


def test_neutral_when_no_expected_range(monkeypatch):
    """纯定性预判（无 expected range）→ neutral。"""
    node = _make_node(monkeypatch, car_0_1=0.05, car_0_5=0.15)
    out = node.run({"event": _event(), "predictions": [_pred("688981", StockDirection.BENEFICIARY, None)]})
    assert out[0].calibration == "neutral"


def test_neutral_when_measure_returns_none(monkeypatch):
    """measure_stock 返回 None → neutral，actual 字段保持 None。"""
    node = _make_node(monkeypatch, m_none=True)
    out = node.run({"event": _event(), "predictions": [_pred("688981", StockDirection.BENEFICIARY, "10%~30%")]})
    assert out[0].calibration == "neutral"
    assert out[0].actual_car_0_5 is None


def test_neutral_when_unresolvable(monkeypatch):
    """标的反查失败 → neutral。"""
    node = _make_node(monkeypatch, car_0_1=0.1, car_0_5=0.2)
    out = node.run({"event": _event(), "predictions": [_pred("999999", StockDirection.BENEFICIARY, "10%~30%")]})
    assert out[0].calibration == "neutral"


# ---- 不可变 ----


def test_does_not_mutate_input(monkeypatch):
    """入参 predictions 对象不应被修改；返回新对象。"""
    node = _make_node(monkeypatch, car_0_1=0.05, car_0_5=0.15)
    pred = _pred("688981", StockDirection.BENEFICIARY, "10%~30%")
    out = node.run({"event": _event(), "predictions": [pred]})
    assert pred.calibration is None  # 入参未被改
    assert pred.actual_car_0_5 is None
    assert out[0] is not pred  # 返回新对象
    assert out[0].calibration == "confirming"
