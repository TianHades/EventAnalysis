"""编排集成测试：7 节点串联（mock Reasoner + 合成行情），验证报告生成 + 池填充。"""

from datetime import date

import numpy as np
import pandas as pd

from eventreplay.adapters.stock_master import ResolvedStock
from eventreplay.pipeline.orchestrator import ReplayOrchestrator
from eventreplay.pipeline.nodes.candidate import _CandOut, _CandsOut
from eventreplay.pipeline.nodes.chain import _ChainNodeOut, _ChainOut
from eventreplay.pipeline.nodes.ingest import _IngestOut
from eventreplay.pipeline.nodes.report import _ReportOut
from eventreplay.pipeline.nodes.synthesis import _SynthOut
from eventreplay.storage import Storage


_DATES = pd.bdate_range("2020-04-01", periods=400).date
_REACTION = _DATES[300]


def _synthetic(signal_per_day: float, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(_DATES)
    ret = rng.normal(0, 0.01, n)
    ret[300 : 300 + 20] += signal_per_day  # 事件窗注入
    close = 100 * np.cumprod(1 + ret)
    return pd.DataFrame(
        {
            "date": _DATES,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
            "turnover": 0.02,
        }
    )


class FakeReasoner:
    model_alias = "fake"

    def call(self, *, system, user, output_model):
        if output_model is _IngestOut:
            return _IngestOut(
                event_type="监管",
                shock_dimension="政策",
                impact_direction="利空",
                key_entities=["K12教培"],
                summary="双减政策",
            )
        if output_model is _ChainOut:
            return _ChainOut(
                nodes=[
                    _ChainNodeOut(label="K12教培", sw_industry="社会服务", level="下游", shock_direction="受损"),
                ]
            )
        if output_model is _CandsOut:
            return _CandsOut(
                candidates=[_CandOut(name="豆神教育", direction="受损", chain_node_label="K12教培", rationale="双减打压教培")]
            )
        if output_model is _SynthOut:
            return _SynthOut(mermaid="graph TD\n  e[双减]-->k[K12教培]", narrative="政策直接打压 K12。")
        if output_model is _ReportOut:
            return _ReportOut(markdown="# 双减复盘报告\n\n（合成）报告内容。")
        raise AssertionError(f"未mock的输出模型: {output_model}")

    def call_text(self, *, system, user, model=None):
        # ReportNode（text 模式）写 ## 1 摘要 + ## 4 判读（===ANALYSIS=== 分隔）；call 保留以兼容其他节点
        return "## 1 事件摘要\n\n双减政策落地复盘摘要。\n\n===ANALYSIS===\n\n## 4 逻辑链判读分析\n\n（合成）分析。"


class FakeStockMaster:
    def resolve(self, identifier: str):
        if "豆神" in identifier or identifier == "300010":
            return ResolvedStock(
                code="300010",
                name="豆神教育",
                market="深",
                board="创业板",
                listing_date=date(2009, 10, 30),
                industry="教育",
            )
        return None

    def is_real_at(self, code, on_date):
        return code == "300010" and on_date > date(2009, 10, 30)


class FakeQuoteAdapter:
    def daily_adjusted(self, code, start, end, adjust="hfq"):
        # 创业板教培注入显著负向 CAR（-30% 量级）
        return _synthetic(signal_per_day=-0.015)

    def index_daily(self, symbol, start, end):
        rng = np.random.default_rng(99)
        close = 3000 * np.cumprod(1 + rng.normal(0, 0.008, len(_DATES)))
        return pd.DataFrame({"date": _DATES, "open": close, "high": close, "low": close, "close": close, "volume": 0})

    def trade_dates(self):
        return list(_DATES)


def test_full_pipeline_end_to_end(tmp_path):
    storage = Storage(tmp_path / "t.db")
    orch = ReplayOrchestrator(
        FakeReasoner(),
        FakeQuoteAdapter(),
        FakeStockMaster(),
        storage=storage,
        reports_dir=tmp_path / "reports",
    )
    markdown, path = orch.replay(
        description="双减政策落地",
        announcement_date="2021-07-24",
        reaction_date=_REACTION.isoformat(),
        event_id="double_reduction",
    )
    assert "双减" in markdown
    assert path.exists()
    # 从 storage 取 chain 验证池
    chain = storage.get_chain("double_reduction")
    assert chain is not None
    assert "300010" in chain.victim_stock_ids, "受损池应包含豆神教育(300010)"


def test_pipeline_cache_skips_second_run(tmp_path):
    """缓存命中：第二次 replay 同事件，FakeReasoner 不应被某些节点再次调用（缓存）。"""
    storage = Storage(tmp_path / "t.db")
    calls = {"n": 0}
    base = FakeReasoner()

    class Counting(base.__class__):
        def call(self, **kw):
            calls["n"] += 1
            return base.call(**kw)

    orch = ReplayOrchestrator(
        Counting(), FakeQuoteAdapter(), FakeStockMaster(), storage=storage, reports_dir=tmp_path / "r"
    )
    orch.replay(description="双减", announcement_date="2021-07-24", reaction_date=_REACTION.isoformat(), event_id="e1")
    first = calls["n"]
    orch.replay(description="双减", announcement_date="2021-07-24", reaction_date=_REACTION.isoformat(), event_id="e1")
    second = calls["n"]
    assert second == first, f"缓存应命中，Reasoner 调用数不应增加（{first}→{second}）"


def test_report_fallback_on_llm_failure():
    """LLM 报告节点超时/失败 → Python 模板兜底，保证报告产出。"""
    from datetime import date as _date

    from eventreplay.pipeline.nodes.report import ReportNode
    from eventreplay.schema import AttributionResult, Event, TransmissionChain

    class BoomReasoner:
        model_alias = "fake"

        def call(self, **kw):
            raise RuntimeError("模拟 LLM 超时")

        def call_text(self, **kw):
            raise RuntimeError("模拟 LLM 超时")

    node = ReportNode(BoomReasoner())
    event = Event(
        id="e",
        description="测试事件",
        announcement_date=_date(2021, 1, 1),
        reaction_date=_date(2021, 1, 1),
        event_type="政策",
    )
    md = node.run(
        {
            "event": event,
            "chain": TransmissionChain(event_id="e"),
            "attribution_result": AttributionResult(),
            "measurements": [],
        }
    )
    assert "模板生成" in md  # 用了兜底模板
    assert "测试事件" in md


def test_replay_market_tag(tmp_path):
    """ReplayOrchestrator(market=...) → event.market 标记（Phase 5 多市场）。"""
    storage = Storage(tmp_path / "t.db")
    orch = ReplayOrchestrator(
        FakeReasoner(), FakeQuoteAdapter(), FakeStockMaster(),
        storage=storage, reports_dir=tmp_path / "reports", market="港股",
    )
    orch.replay(
        description="腾讯大股东减持",
        announcement_date="2023-01-25",
        reaction_date=_REACTION.isoformat(),
        event_id="hk_evt",
    )
    e = storage.get_event("hk_evt")
    assert e is not None
    assert e.market == "港股"


# ---- Fix 9a: Replay 状态接线 ----


def test_replay_status_done_on_success(tmp_path):
    """成功 → replays 表 status='done'（finished_at 落值）。"""
    storage = Storage(tmp_path / "t.db")
    orch = ReplayOrchestrator(
        FakeReasoner(), FakeQuoteAdapter(), FakeStockMaster(),
        storage=storage, reports_dir=tmp_path / "reports",
    )
    orch.replay(
        description="双减政策落地", announcement_date="2021-07-24",
        reaction_date=_REACTION.isoformat(), event_id="ok_evt",
    )
    r = storage.get_replay("ok_evt")
    assert r is not None
    assert r.status == "done"
    assert r.finished_at is not None
    assert r.event_id == "ok_evt"


def test_replay_status_failed_and_reraise(tmp_path):
    """中途节点失败 → status='failed' 落库后原异常 re-raise（半成品可辨识）。"""
    import pytest

    class FailSynthReasoner(FakeReasoner):
        def call(self, *, system, user, output_model):
            if output_model is _SynthOut:
                raise RuntimeError("模拟 synthesis 失败")
            return super().call(system=system, user=user, output_model=output_model)

    storage = Storage(tmp_path / "t.db")
    orch = ReplayOrchestrator(
        FailSynthReasoner(), FakeQuoteAdapter(), FakeStockMaster(),
        storage=storage, reports_dir=tmp_path / "reports",
    )
    with pytest.raises(RuntimeError, match="synthesis 失败"):
        orch.replay(
            description="某政策冲击", announcement_date="2022-03-01",
            reaction_date=_REACTION.isoformat(), event_id="fail_evt",
        )
    r = storage.get_replay("fail_evt")
    assert r is not None
    assert r.status == "failed"


# ---- EventIngest：盘后 → 次交易日 + 日期解析容错 ----


def test_ingest_after_close_reaction_next_day():
    """盘后公告 → reaction_date = announcement_date + 1 天；显式 reaction_date 优先。"""
    from eventreplay.pipeline.nodes.ingest import EventIngestNode, _IngestOut

    class _ATReasoner:
        model_alias = "fake"

        def call(self, *, system, user, output_model):
            assert output_model is _IngestOut
            return _IngestOut(event_type="政策", announcement_time="盘后")

    node = EventIngestNode(_ATReasoner())
    inputs = {"description": "盘后出的政策", "announcement_date": "2021-07-23"}
    e = node.run(inputs)
    assert e.reaction_date == date(2021, 7, 24)  # 盘后 → 次日
    assert e.announcement_date == date(2021, 7, 23)
    assert e.announcement_time.value == "盘后"

    # 显式 reaction_date 优先级不变
    inputs2 = {"description": "盘后出的政策", "announcement_date": "2021-07-23", "reaction_date": "2021-07-30"}
    e2 = node.run(inputs2)
    assert e2.reaction_date == date(2021, 7, 30)


def test_ingest_invalid_date_raises_clear_error():
    """announcement_date 不可解析 → 抛带清晰信息的 ValueError（节点层兜底）。"""
    import pytest

    from eventreplay.pipeline.nodes.ingest import EventIngestNode, _IngestOut

    class _R:
        model_alias = "fake"

        def call(self, *, system, user, output_model):
            return _IngestOut(event_type="政策")

    node = EventIngestNode(_R())
    with pytest.raises(ValueError, match="公告日期不可解析"):
        node.run({"description": "x", "announcement_date": "not-a-date"})
