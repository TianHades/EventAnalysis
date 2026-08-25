"""ForwardOrchestrator 测试：三层降级路由 + 端到端前瞻报告产出。

验收（计划 P3/P4）：
- mock find_similar 返回高/低/空 → 断言走 strong/weak/qualitative 路径
- 端到端 pre + strong → 前瞻报告含预期 CAR 区间
- 端到端 qualitative → 复用 IndustryChain + CandidateGen → 纯定性报告
- 端到端 post + strong → 校准列有值
"""

from datetime import date, datetime

import pandas as pd

from eventreplay.adapters.stock_master import ResolvedStock
from eventreplay.llm import LLMNodeFailed
from eventreplay.pipeline.forward_orchestrator import ForwardOrchestrator
from eventreplay.pipeline.nodes.candidate import _CandOut, _CandsOut
from eventreplay.pipeline.nodes.chain import _ChainNodeOut, _ChainOut
from eventreplay.pipeline.nodes.forward_mapping import _BenefOut, _PredictedStockOut, _VictOut
from eventreplay.pipeline.nodes.ingest import _IngestOut
from eventreplay.schema import Event, ImpactDirection, ShockDimension, StockDirection
from eventreplay.schema.pattern import ArchetypalStock, Pattern

_DATES = pd.bdate_range("2026-06-01", periods=40).date
_REACTION = date(2026, 7, 15)


class FakeReasoner:
    """按 output_model 分发；同时覆盖前瞻路径与定性路径。"""

    model_alias = "fake"

    def call(self, *, system, user, output_model):
        if output_model is _IngestOut:
            return _IngestOut(
                event_type="地缘", shock_dimension="政策", impact_direction="混合",
                key_entities=["芯片"], summary="芯片管制升级",
            )
        if output_model is _BenefOut:
            return _BenefOut(
                transmission_logic="管制升级→国产替代受益",
                beneficiaries=[
                    _PredictedStockOut(
                        code="688981", name="中芯国际", direction="受益",
                        expected_car_0_20="10%~30%", expected_car_0_60="20%~50%",
                        rationale="国产替代核心载体",
                    )
                ],
                confidence="高", key_risks="管制豁免 / 国产化不及预期",
            )
        if output_model is _VictOut:
            return _VictOut(
                transmission_logic="海外设备依赖→扩产受阻",
                victims=[
                    _PredictedStockOut(
                        code="000725", name="京东方A", direction="受损",
                        expected_car_0_20="-5%~-15%", expected_car_0_60="-10%~-25%",
                        rationale="海外设备依赖",
                    )
                ],
            )
        if output_model is _ChainOut:
            return _ChainOut(nodes=[
                _ChainNodeOut(label="晶圆制造", sw_industry="电子", level="中游", shock_direction="受益")
            ])
        if output_model is _CandsOut:
            return _CandsOut(candidates=[
                _CandOut(name="中芯国际", direction="受益", chain_node_label="晶圆制造", rationale="国产替代")
            ])
        raise AssertionError(f"未mock的输出模型: {output_model}")


class FakeStockMaster:
    def resolve(self, identifier: str):
        if identifier in ("688981", "中芯国际"):
            return ResolvedStock(
                code="688981", name="中芯国际", market="沪", board="科创板",
                listing_date=date(2020, 7, 16),
            )
        if identifier in ("000725", "京东方A"):
            return ResolvedStock(
                code="000725", name="京东方A", market="深", board="主板",
                listing_date=date(2000, 1, 1),
            )
        return None


class FakeQuoteAdapter:
    def daily_adjusted(self, code, start, end, adjust="hfq"):
        close = [100.0 if d <= _REACTION else 115.0 for d in _DATES]
        return pd.DataFrame({
            "date": _DATES, "open": close, "high": close, "low": close,
            "close": close, "volume": [1e6] * len(_DATES),
        })

    def index_daily(self, symbol, start, end):
        close = [3000.0] * len(_DATES)
        return pd.DataFrame({
            "date": _DATES, "open": close, "high": close, "low": close,
            "close": close, "volume": [0] * len(_DATES),
        })

    def trade_dates(self):
        return list(_DATES)


class FakePatternService:
    """受控 find_similar：返回构造时给定的 (Pattern, score) 列表 + 降级标记。"""

    def __init__(self, results, degraded: bool = False, storage=None):
        self._results = results
        self._degraded = degraded
        self.storage = storage

    def find_similar(self, desc, *, top_k=3, **kw):
        return self._results[:top_k], self._degraded


def _pattern() -> Pattern:
    return Pattern(
        id="p1", source_replay_id="replay-chip-2018", created_at=datetime(2026, 7, 1),
        model_alias="fake", prompt_hash="x", shock_dimension=ShockDimension.POLICY,
        impact_direction=ImpactDirection.NEGATIVE, event_type="地缘", industries=["电子"],
        event_summary="中美芯片摩擦", transmission_template="管制升级→国产替代受益",
        archetypal_beneficiaries=[
            ArchetypalStock(industry_node="晶圆制造", stocks=["688981"], observed_car_0_20=0.2)
        ],
        archetypal_victims=[],
    )


def _orch(tmp_path, results):
    return _orch_with(FakeReasoner(), tmp_path, results)


def _orch_with(reasoner, tmp_path, results):
    return ForwardOrchestrator(
        reasoner, FakeQuoteAdapter(), FakeStockMaster(),
        pattern_service=FakePatternService(results), storage=None,
        reports_dir=tmp_path / "reports",
    )


def _orch_degraded(tmp_path, results, degraded, storage=None):
    return ForwardOrchestrator(
        FakeReasoner(), FakeQuoteAdapter(), FakeStockMaster(),
        pattern_service=FakePatternService(results, degraded=degraded, storage=storage),
        storage=storage,
        reports_dir=tmp_path / "reports",
    )


# ---- 三层路由 ----


def test_routing_strong(tmp_path):
    """top-1 > 0.6 → 强 Pattern 路径 + 预期 CAR 区间 + 报告落盘。"""
    md, path = _orch(tmp_path, [(_pattern(), 0.82)]).forward(
        description="美国对华芯片管制升级", date_str="2026-07-15",
        mode="pre", event_id="fwd-strong",
    )
    assert path.exists()
    assert "强 Pattern" in md
    assert "盘前前瞻" in md
    assert "688981" in md and "中芯国际" in md
    assert "10%~30%" in md  # 预期 CAR 区间
    assert "高" in md  # 置信度
    assert "实际[+1]" not in md  # 盘前不校准


def test_routing_weak(tmp_path):
    """0.3 < top-1 ≤ 0.6 → 弱 Pattern 路径。"""
    md, _ = _orch(tmp_path, [(_pattern(), 0.50)]).forward(
        description="芯片管制", date_str="2026-07-15", mode="pre", event_id="fwd-weak"
    )
    assert "弱 Pattern" in md


def test_routing_qualitative_empty(tmp_path):
    """规律库空 → 纯定性路径（复用 IndustryChain + CandidateGen）。"""
    md, _ = _orch(tmp_path, []).forward(
        description="全新事件无历史类比", date_str="2026-07-15",
        mode="pre", event_id="fwd-qual",
    )
    assert "纯定性" in md
    assert "10%~30%" not in md  # 纯定性无预期 CAR
    assert "688981" in md  # 复用 CandidateGen 产出


def test_routing_qualitative_low_score(tmp_path):
    """top-1 < 0.3 → 纯定性路径。"""
    md, _ = _orch(tmp_path, [(_pattern(), 0.15)]).forward(
        description="弱相似", date_str="2026-07-15", mode="pre", event_id="fwd-low"
    )
    assert "纯定性" in md


# ---- 端到端：盘后校准 ----


def test_post_mode_calibrates_strong(tmp_path, monkeypatch):
    """post + strong → 校准列有值，confirming 出现在报告。"""
    from eventreplay.schema import ImpactMeasurement

    def _fake_measure(**kw):
        # 受益标的 +15% → confirming；受损标的 +15%（与预期相反）→ diverging
        return ImpactMeasurement(
            stock_id=kw["stock_id"], episode_id=kw["episode_id"],
            car_0_1=0.05, car_0_5=0.15,
        )
    monkeypatch.setattr(
        "eventreplay.pipeline.nodes.calibration.measure_stock", _fake_measure
    )

    md, _ = _orch(tmp_path, [(_pattern(), 0.82)]).forward(
        description="芯片管制", date_str="2026-07-15",
        mode="post", event_id="fwd-post",
    )
    assert "盘后校准" in md
    assert "实际[+1]" in md and "实际[+5]" in md  # 校准列表头
    assert "吻合" in md  # 受益标的 +15% vs 预期 10%~30% → confirming（渲染为中文标签）


# ---- 路由阈值边界 ----


def test_strong_threshold_boundary(tmp_path):
    """score 恰为 0.6 → 不大于 0.6 → weak（边界归 weak）。"""
    md, _ = _orch(tmp_path, [(_pattern(), 0.60)]).forward(
        description="边界", date_str="2026-07-15", mode="pre", event_id="fwd-b1"
    )
    assert "弱 Pattern" in md


def test_weak_threshold_boundary(tmp_path):
    """score=0.30 < 0.45(WEAK_THRESHOLD) → qualitative。"""
    md, _ = _orch(tmp_path, [(_pattern(), 0.30)]).forward(
        description="边界", date_str="2026-07-15", mode="pre", event_id="fwd-b2"
    )
    assert "纯定性" in md


class FailMappingReasoner:
    """ingest 成功，forward_mapping 子调用总失败（模拟 sonnet 超时重试耗尽）。"""

    model_alias = "fake"

    def call(self, *, system, user, output_model):
        if output_model is _IngestOut:
            return _IngestOut(
                event_type="地缘", shock_dimension="政策", impact_direction="混合",
                key_entities=["芯片"], summary="芯片管制",
            )
        if output_model in (_BenefOut, _VictOut):
            raise LLMNodeFailed("claude 退出码 1: 超时")
        raise AssertionError(f"未mock: {output_model}")


def test_forward_mapping_failure_shows_banner(tmp_path):
    """forward_mapping 重试耗尽 → 报告显著 banner + 预判空。"""
    md, _ = _orch_with(FailMappingReasoner(), tmp_path, [(_pattern(), 0.82)]).forward(
        description="芯片管制", date_str="2026-07-15", mode="pre", event_id="fwd-fail"
    )
    assert "forward_mapping 推理失败" in md
    assert "受益方向预判失败" in md
    assert "受损方向预判失败" in md
    assert "预判结果可能不完整" in md
    # 预判表为空（无标的行）
    assert "688981" not in md
    assert "000725" not in md


def test_forward_report_shows_pattern_qualitative(tmp_path):
    """前瞻报告 ## 2 展示历史 Pattern 的事件影响定性（供本次类比参考）。"""
    from eventreplay.schema import PatternQualitative

    p = _pattern().model_copy(update={"qualitative": PatternQualitative(
        magnitude="大", scope="广", persistence="中期", significance="显著",
        rationale="历史影响大且广",
    )})
    md, _ = _orch(tmp_path, [(p, 0.82)]).forward(
        description="芯片管制升级", date_str="2026-07-15",
        mode="pre", event_id="fwd-qual-disp",
    )
    assert "事件影响定性" in md  # ## 2 标题
    assert "程度" in md and "范围" in md and "持续" in md  # 表头
    assert "大" in md and "广" in md and "显著" in md  # 定性值


# ---- Fix 5c + Fix 12: 检索三态与报告显示 ----


def test_forward_degraded_banner_no_false_claim(tmp_path):
    """检索不可用 → 报告明示"向量检索不可用"，且不得谎称"无历史 Pattern 类比"。"""
    md, _ = _orch_degraded(tmp_path, [], degraded=True).forward(
        description="芯片管制", date_str="2026-07-15", mode="pre", event_id="fwd-deg",
    )
    assert "向量检索不可用" in md
    assert "纯定性推理" in md
    assert "无历史 Pattern 类比" not in md, "检索不可用时不得谎称已比对过历史"


def test_forward_lib_empty_distinguished_from_no_match(tmp_path):
    """库空（storage 无 Pattern）→ 报告说"规律库为空"；有库但无匹配 → 说"无相似"。"""
    from eventreplay.storage import Storage

    st = Storage(tmp_path / "lib.db")
    md, _ = _orch_degraded(tmp_path, [], degraded=False, storage=st).forward(
        description="全新事件", date_str="2026-07-15", mode="pre", event_id="fwd-empty",
    )
    assert "规律库为空" in md

    st.save_pattern(_pattern())  # 库里有 1 条但相似度不足（results 为空 → 无匹配）
    md2, _ = _orch_degraded(tmp_path, [], degraded=False, storage=st).forward(
        description="全新事件", date_str="2026-07-15", mode="pre", event_id="fwd-nomatch",
    )
    assert "无相似历史 Pattern" in md2


def test_forward_mode_not_post_no_calibration_label(tmp_path):
    """mode≠post（如盘中 intraday）时报告头不得出现"盘后校准"字样。"""
    md, _ = _orch(tmp_path, [(_pattern(), 0.82)]).forward(
        description="芯片管制", date_str="2026-07-15", mode="intraday", event_id="fwd-intra",
    )
    assert "盘后校准" not in md
    assert "intraday" in md  # 未知 mode 原样展示（不冒充盘前/盘后）


def test_forward_report_renders_none_fields_safely(tmp_path):
    """qualitative 各字段为 None 时渲染为 '—'，不崩溃（下游 schema 正 Optional 化）。"""
    from eventreplay.schema import ForwardPrediction, ForwardResult

    orch = _orch(tmp_path, [])
    ev = _pattern().model_copy()  # 任意 Event
    event = Event(
        id="e", description="测试事件", announcement_date=date(2026, 7, 15),
        reaction_date=date(2026, 7, 15), event_type="政策",
    )
    preds = [ForwardPrediction(stock_id="600000", stock_name=None, direction=StockDirection.BENEFICIARY, rationale="r")]
    result = ForwardResult(event_id="x", event=event, beneficiary_predictions=preds)
    # 模拟 Optional 化后的 None 字段（model_copy 不做校验）
    result = result.model_copy(update={"transmission_logic": None, "confidence": None, "key_risks": None})
    md = orch._generate_report(result)
    assert "—" in md  # None 渲染为占位符而非崩溃
    assert "None" not in md.replace("stock_name=None", "")  # 不出现裸 None 字样
