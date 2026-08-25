"""ImpactMeasurementNode 测试：skip 日志 replay_id 来源 + 多 episode 全量度量。"""

from datetime import date, timedelta

import pandas as pd

from eventreplay.adapters.stock_master import ResolvedStock
from eventreplay.schema import Event, EventEpisode, ImpactMeasurement, StockDirection


class FakeSM:
    def resolve(self, identifier):
        if identifier == "300010":
            return ResolvedStock(code="300010", name="豆神教育", market="深", board="创业板",
                                 listing_date=date(2009, 10, 30))
        return None

    def is_real_at(self, code, on_date):
        return code == "300010"


class FakeQA:
    def daily_adjusted(self, code, start, end, adjust="hfq"):
        return pd.DataFrame({"date": [date(2021, 7, 1)], "close": [10.0]})


class FakeStorage:
    def __init__(self):
        self.skips = []

    def append_skip(self, *, replay_id, stock_id=None, episode_id=None, reason=""):
        self.skips.append({"replay_id": replay_id, "stock_id": stock_id, "reason": reason})


def _event(episodes: list[EventEpisode] | None = None) -> Event:
    e = Event(
        id="evt-x", description="测试事件",
        announcement_date=date(2021, 7, 1), reaction_date=date(2021, 7, 1),
        event_type="政策",
    )
    if episodes:
        e = e.model_copy(update={"episodes": episodes})
    return e


def _make_node(monkeypatch, measured):
    from eventreplay.pipeline.nodes.impact import ImpactMeasurementNode

    node = ImpactMeasurementNode(FakeQA(), FakeSM(), storage=None)
    monkeypatch.setattr(
        "eventreplay.pipeline.nodes.impact.measure_stock",
        lambda **kw: ImpactMeasurement(stock_id=kw["stock_id"], episode_id=kw["episode_id"], car_0_20=measured.get(kw["episode_id"])),
    )
    return node


def test_skip_log_uses_replay_id_from_inputs(monkeypatch):
    """skip 日志的 replay_id 取 inputs['_replay_id']（非硬编码 'adhoc'）。"""
    node = _make_node(monkeypatch, {})
    st = FakeStorage()
    node.storage = st
    node.run({
        "event": _event(),
        "candidates": [{"stock_id": "999999", "direction": StockDirection.VICTIM}],
        "bench_df": pd.DataFrame(),
        "_replay_id": "double_reduction",
    })
    assert st.skips, "反查失败应记 skip"
    assert all(s["replay_id"] == "double_reduction" for s in st.skips)


def test_measures_all_episodes_not_only_first(monkeypatch):
    """多 episode 事件：遍历全部子事件度量（代表子事件取 |car_0_20| 最大由下游从全量度量中选）。"""
    eps = [
        EventEpisode(id="evt-x-ep0", announcement_date=date(2021, 7, 1), reaction_date=date(2021, 7, 1)),
        EventEpisode(id="evt-x-ep1", announcement_date=date(2021, 8, 1), reaction_date=date(2021, 8, 2)),
    ]
    node = _make_node(monkeypatch, {"evt-x-ep0": -0.05, "evt-x-ep1": -0.30})
    results = node.run({
        "event": _event(eps),
        "candidates": [{"stock_id": "300010", "direction": StockDirection.VICTIM}],
        "bench_df": pd.DataFrame(),
        "_replay_id": "r2",
    })
    by_ep = {m.episode_id: m for m in results}
    assert set(by_ep) == {"evt-x-ep0", "evt-x-ep1"}  # 两个子事件都有度量
    # |car_0_20| 最大的 ep1 可从全量结果中选出作为代表
    rep = max(results, key=lambda m: abs(m.car_0_20 or 0))
    assert rep.episode_id == "evt-x-ep1"


def test_results_ordered_representative_first(monkeypatch):
    """同标的多 episode：结果按 |car_0_20| 降序——下游首见去重（attribution）即取代表度量。"""
    eps = [
        EventEpisode(id="evt-x-ep0", announcement_date=date(2021, 7, 1), reaction_date=date(2021, 7, 1)),
        EventEpisode(id="evt-x-ep1", announcement_date=date(2021, 8, 1), reaction_date=date(2021, 8, 2)),
    ]
    node = _make_node(monkeypatch, {"evt-x-ep0": -0.05, "evt-x-ep1": -0.30})
    results = node.run({
        "event": _event(eps),
        "candidates": [{"stock_id": "300010", "direction": StockDirection.VICTIM}],
        "bench_df": pd.DataFrame(),
        "_replay_id": "r3",
    })
    # 首见即代表：ep1（|car_0_20|=0.30）必须排在 ep0（0.05）前
    assert results[0].episode_id == "evt-x-ep1"
