"""QualitativeForecastNode 测试：三层分工（程序锚点 / LLM 判定 / 事后对账）。

验收：
- 证据层：collect_anchors 计算 bench/个股事件前 20 日收益（含超额）、先例五维众数；全容错
- 判定层：LLM 路径产出五维 + 逐维置信度；LLM 失败 → 先例众数兜底（source=fallback）
- schema：非法档位 → None（与 PatternQualitative 同口径）
- 对账：calibrate_qualitative 逐维 一致/不一致/缺失 + hit_rate
- 报告：§1.5 渲染五维表 + fallback banner
"""

from datetime import date, datetime

import pandas as pd

from eventreplay.llm import LLMNodeFailed
from eventreplay.pipeline.nodes.calibration import calibrate_qualitative
from eventreplay.pipeline.nodes.qualitative_forecast import (
    QualitativeForecastNode,
    collect_anchors,
    _ForecastOut,
)
from eventreplay.schema import Event, ForwardPrediction, StockDirection
from eventreplay.schema.forward import QualitativeForecast
from eventreplay.schema.pattern import Pattern, PatternQualitative

_REACTION = date(2026, 7, 15)
_DATES = pd.bdate_range("2026-05-01", periods=60).date  # 覆盖事件前 20 交易日的窗口


def _event() -> Event:
    return Event(
        id="fwd-test", description="测试事件", market="A股", event_type="政策",
        announcement_date=_REACTION, reaction_date=_REACTION,
    )


def _pattern(magnitude="中", surprise="符合预期") -> Pattern:
    return Pattern(
        id="p1", source_replay_id="replay-x", created_at=datetime(2026, 7, 1),
        model_alias="fake", prompt_hash="x", event_type="政策",
        event_summary="测试先例", transmission_template="a→b",
        qualitative=PatternQualitative(
            magnitude=magnitude, scope="中", persistence="短期脉冲",
            significance="不显著", surprise=surprise, rationale="",
        ),
    )


class _ClimbingQA:
    """指数/个股事件前缓慢爬升（事件前 20 交易日 +10%/+16%），事件日跳空。"""

    def daily_adjusted(self, code, start, end, adjust="hfq"):
        base = 100.0 if code == "601398" else 200.0
        closes = []
        for i, d in enumerate(_DATES):
            c = base * (1 + 0.0045 * i) if d < _REACTION else base * 1.15
            closes.append(c)
        return pd.DataFrame({"date": _DATES, "close": closes})

    def index_daily(self, symbol, start, end):
        closes = [3000.0 * (1 + 0.0045 * i) if d < _REACTION else 3450.0 for i, d in enumerate(_DATES)]
        return pd.DataFrame({"date": _DATES, "close": closes})


class _DeadQA:
    """行情全挂 → 锚点收集必须容错（不抛异常）。"""

    def daily_adjusted(self, *a, **kw):
        raise TimeoutError("dead")

    def index_daily(self, *a, **kw):
        raise TimeoutError("dead")


class FakeForecastReasoner:
    """受控 LLM：按 raise_me 决定返回合法五维或抛 LLMNodeFailed。记录 user prompt。"""

    model_alias = "fake"
    calls: list[str] = []

    def __init__(self, raise_me: bool = False):
        self.raise_me = raise_me

    def call(self, *, system, user, output_model):
        FakeForecastReasoner.calls.append(user)
        if self.raise_me:
            raise LLMNodeFailed("boom")
        assert output_model is _ForecastOut
        return _ForecastOut(
            magnitude="中", scope="窄", persistence="短期脉冲",
            significance="不显著", surprise="符合预期",
            dim_confidence={"surprise": "高", "magnitude": "中"},
            rationale="额度入预算且当事标的已提前定价，判定符合预期",
        )


def _node(reasoner, qa=None) -> QualitativeForecastNode:
    return QualitativeForecastNode(reasoner, qa or _ClimbingQA(), storage=None)


# ---- 证据层 ----

def test_collect_anchors_bench_and_targets():
    preds = [ForwardPrediction(stock_id="601398", direction=StockDirection.BENEFICIARY, rationale="")]
    anchors = collect_anchors(_event(), [_pattern()], preds, _ClimbingQA(), "sh000300")
    # 事件前 20 交易日基准收益：19 根间隔 × 0.45% ≈ +8.9%（1.0045^19-1）
    assert anchors["bench_pre_20"].startswith("+")
    assert "601398" in anchors["targets_pre_20"]
    # 量纲回归：个股与基准同速爬升 → 超额 ≈ +0.0%（曾出 +338% 的百分数/小数混减 bug）
    assert "超额+0.0%" in anchors["targets_pre_20"]["601398"]
    # 先例众数
    assert anchors["pattern_majority"]["magnitude"] == "中"


def test_collect_anchors_dead_adapter_no_crash():
    anchors = collect_anchors(_event(), [], [], _DeadQA(), "sh000300")
    assert "bench_pre_20" not in anchors  # 容错：缺锚点而非炸掉


# ---- 判定层 ----

def test_node_llm_path():
    FakeForecastReasoner.calls = []
    node = _node(FakeForecastReasoner())
    out = node.run({"event": _event(), "patterns": [_pattern()], "beneficiaries": []})
    assert isinstance(out, QualitativeForecast)
    assert out.source == "llm"
    assert out.magnitude == "中" and out.surprise == "符合预期"
    assert out.dim_confidence["surprise"] == "高"
    assert out.anchors_used  # 程序锚点已注入
    assert "bench_pre_20" in FakeForecastReasoner.calls[0]  # user prompt 含锚点


def test_node_fallback_mode_majority():
    node = _node(FakeForecastReasoner(raise_me=True))
    patterns = [_pattern(magnitude="中"), _pattern(magnitude="中"), _pattern(magnitude="大")]
    out = node.run({"event": _event(), "patterns": patterns, "beneficiaries": []})
    assert out.source == "fallback"
    assert out.magnitude == "中"  # 众数
    assert all(out.dim_confidence.get(k) == "低" for k in out.dim_confidence)


def test_node_fallback_no_patterns():
    node = _node(FakeForecastReasoner(raise_me=True))
    out = node.run({"event": _event(), "patterns": [], "beneficiaries": []})
    assert out.source == "fallback"
    assert out.magnitude is None and out.surprise is None


# ---- schema ----

def test_schema_invalid_label_to_none():
    f = QualitativeForecast(magnitude="巨大", scope="广", persistence="短期脉冲",
                            significance="显著", surprise="符合预期")
    assert f.magnitude is None
    assert f.scope == "广"
    # dim_confidence 过滤：非五维键 / 非高中低值 → 丢弃（缓存重建/外部构造兜底）
    f2 = QualitativeForecast(dim_confidence={"surprise": "高", "foo": "高", "magnitude": "极高"})
    assert f2.dim_confidence == {"surprise": "高"}


# ---- 校准层 ----

def test_calibrate_qualitative_mixed():
    fc = QualitativeForecast(magnitude="中", scope="窄", persistence="短期脉冲",
                             significance="不显著", surprise="符合预期")
    ac = PatternQualitative(magnitude="中", scope="广", persistence="短期脉冲",
                            significance=None, surprise="符合预期")
    cal = calibrate_qualitative(fc, ac)
    assert cal.dim_results["magnitude"] == "一致"
    assert cal.dim_results["scope"] == "不一致"
    assert cal.dim_results["significance"] == "实测缺失"
    # 可对账维度：magnitude/scope/persistence/surprise（significance 实测缺失不计）
    assert cal.hit_rate == 3 / 4


# ---- 报告渲染 ----

def test_report_section_rendered(tmp_path):
    from eventreplay.pipeline.forward_orchestrator import ForwardOrchestrator

    class _Orch(ForwardOrchestrator):
        """绕过真实构造（只要 _generate_report）。"""

        def __init__(self):  # noqa: D107
            pass

    orch = _Orch()
    orch.ps = None  # _generate_report 的 _pattern_lib_empty 需要这两个属性
    orch.storage = None
    from eventreplay.schema import ForwardResult

    result = ForwardResult(
        event_id="e", event=_event(), mode="pre", degradation_level="qualitative",
        qualitative_forecast=QualitativeForecast(
            magnitude="中", scope="窄", persistence="短期脉冲",
            significance="不显著", surprise="符合预期",
            dim_confidence={"surprise": "高"},
            rationale="预期内事件", anchors_used=["bench_pre_20=+8.9%"],
        ),
    )
    md = orch._generate_report(result)
    assert "## 1.5 事件定性预判" in md
    assert "符合预期" in md and "高" in md
    assert "bench_pre_20=+8.9%" in md
    assert "⚠️" not in md  # 非 fallback 无警示

    result2 = result.model_copy(update={"qualitative_forecast": QualitativeForecast(
        source="fallback", rationale="LLM 失败，先例众数兜底",
    )})
    md2 = orch._generate_report(result2)
    assert "程序兜底" in md2

    result3 = result.model_copy(update={"qualitative_forecast": None})
    md3 = orch._generate_report(result3)
    assert "定性预判节点未产出" in md3

    # E2 回归：异常摘要写入报告（不静默吞）
    result4 = result.model_copy(update={
        "qualitative_forecast": None,
        "qualitative_forecast_error": "KeyError: 'event'",
    })
    assert "KeyError: 'event'" in orch._generate_report(result4)
