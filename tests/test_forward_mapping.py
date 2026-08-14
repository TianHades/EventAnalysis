"""ForwardMappingNode 测试：拆分两次子调用（受益/受损）+ 反查过滤 + 合并。

验收（计划 P1）：mock Reasoner + mock Pattern → 断言预判列表。
拆分版：FakeReasoner 按 output_model 分发 _BenefOut/_VictOut，断言合并后产出。
"""

from datetime import date, datetime

from eventreplay.adapters.stock_master import ResolvedStock
from eventreplay.llm import LLMNodeFailed
from eventreplay.pipeline.nodes.forward_mapping import (
    ForwardMappingNode,
    ForwardMappingResult,
    _BenefOut,
    _PredictedStockOut,
    _VictOut,
)
from eventreplay.schema import Event, ImpactDirection, ShockDimension, StockDirection
from eventreplay.schema.pattern import ArchetypalStock, Pattern


class FakeReasoner:
    model_alias = "fake"

    def call(self, *, system, user, output_model):
        if output_model is _BenefOut:
            return _BenefOut(
                transmission_logic="管制升级→国产替代受益",
                beneficiaries=[
                    _PredictedStockOut(
                        code="688981", name="中芯国际", direction="受益",
                        expected_car_0_20="10%~30%", expected_car_0_60="20%~50%",
                        rationale="国产替代核心载体",
                    ),
                    _PredictedStockOut(
                        code="999999", name="不存在的票", direction="受益",
                        rationale="应被反查过滤",
                    ),
                    _PredictedStockOut(
                        code="688981", name="中芯国际", direction="无效方向",
                        rationale="方向枚举不合法，应被过滤",
                    ),
                ],
                confidence="高",
                key_risks="管制豁免 / 国产化进度不及预期",
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
        raise AssertionError(f"未mock的输出模型: {output_model}")


class FakeStockMaster:
    _mapping = {
        "688981": ("688981", "中芯国际"),
        "中芯国际": ("688981", "中芯国际"),
        "000725": ("000725", "京东方A"),
        "京东方A": ("000725", "京东方A"),
    }

    def resolve(self, identifier: str):
        hit = self._mapping.get(identifier)
        if hit is None:
            return None
        return ResolvedStock(
            code=hit[0], name=hit[1], market="沪", board="科创板",
            listing_date=date(2020, 7, 16),
        )


def _pattern() -> Pattern:
    return Pattern(
        id="p1",
        source_replay_id="replay-chip-2018",
        created_at=datetime(2026, 7, 1),
        model_alias="fake",
        prompt_hash="x",
        shock_dimension=ShockDimension.POLICY,
        impact_direction=ImpactDirection.NEGATIVE,
        event_type="地缘",
        industries=["电子"],
        event_summary="中美芯片摩擦",
        transmission_template="管制升级→国产替代受益",
        archetypal_beneficiaries=[
            ArchetypalStock(industry_node="晶圆制造", stocks=["688981"], observed_car_0_20=0.2)
        ],
        archetypal_victims=[
            ArchetypalStock(industry_node="面板", stocks=["000725"], observed_car_0_20=-0.1)
        ],
    )


def _event() -> Event:
    return Event(
        id="e",
        description="美国对华芯片管制升级",
        announcement_date=date(2026, 7, 15),
        reaction_date=date(2026, 7, 15),
        event_type="地缘",
        shock_dimension=ShockDimension.POLICY,
        impact_direction=ImpactDirection.MIXED,
    )


def test_forward_mapping_split_and_merge():
    """拆分两次子调用 → 合并 + 反查过滤 + 方向校验 + 来源溯源。"""
    node = ForwardMappingNode(FakeReasoner(), FakeStockMaster(), storage=None)
    out = node.run({
        "event": _event(),
        "patterns": [_pattern()],
        "scores": [0.82],
        "degradation_level": "strong",
    })

    # 返回模型契约（output_type 声明保证 SQLite 缓存命中后重建为同类型）
    assert isinstance(out, ForwardMappingResult)
    assert out.errors == []
    assert out.confidence == "高"

    # 受益池：第一条合法（第二条反查失败、第三条方向非法）→ 1 条
    benef = out.beneficiary_predictions
    assert len(benef) == 1
    b = benef[0]
    assert b.stock_id == "688981"
    assert b.stock_name == "中芯国际"
    assert b.direction == StockDirection.BENEFICIARY
    assert b.expected_car_0_20_range == "10%~30%"
    assert b.source_pattern_ids == ["replay-chip-2018"]

    # 受损池
    vict = out.victim_predictions
    assert len(vict) == 1
    assert vict[0].stock_id == "000725"
    assert vict[0].direction == StockDirection.VICTIM
    assert vict[0].expected_car_0_20_range == "-5%~-15%"

    # 传导逻辑合并：受益侧 + 受损侧
    assert "国产替代" in out.transmission_logic
    assert "受损侧" in out.transmission_logic
    assert "海外设备依赖" in out.transmission_logic


def test_forward_mapping_tolerates_empty_patterns():
    """空 Pattern 列表不应崩溃。"""
    node = ForwardMappingNode(FakeReasoner(), FakeStockMaster(), storage=None)
    out = node.run({
        "event": _event(), "patterns": [], "scores": [], "degradation_level": "weak",
    })
    assert len(out.beneficiary_predictions) == 1  # FakeReasoner 固定返回


class _RetryReasoner:
    """受益子调用第一次失败（超时），重试成功；受损一次成功。"""

    model_alias = "fake"

    def __init__(self):
        self.benef_calls = 0

    def call(self, *, system, user, output_model):
        if output_model is _BenefOut:
            self.benef_calls += 1
            if self.benef_calls == 1:
                raise LLMNodeFailed("claude 退出码 1: 超时")
            return _BenefOut(
                transmission_logic="国产替代受益",
                beneficiaries=[_PredictedStockOut(
                    code="688981", name="中芯国际", direction="受益",
                    expected_car_0_20="10%~30%", rationale="国产替代",
                )],
                confidence="高", key_risks="风险",
            )
        if output_model is _VictOut:
            return _VictOut(
                transmission_logic="海外依赖受损",
                victims=[_PredictedStockOut(
                    code="000725", name="京东方A", direction="受损",
                    expected_car_0_20="-5%~-15%", rationale="海外依赖",
                )],
            )
        raise AssertionError(f"未mock: {output_model}")


def test_forward_mapping_retry_then_success():
    """子调用第一次超时，重试后成功 → errors 空，预判完整。"""
    node = ForwardMappingNode(_RetryReasoner(), FakeStockMaster(), storage=None)
    out = node.run({
        "event": _event(), "patterns": [_pattern()], "scores": [0.82], "degradation_level": "strong",
    })
    assert out.errors == []
    assert len(out.beneficiary_predictions) == 1
    assert len(out.victim_predictions) == 1


class _AlwaysFailReasoner:
    model_alias = "fake"

    def call(self, *, system, user, output_model):
        raise LLMNodeFailed("claude 退出码 1: 超时")


def test_forward_mapping_all_fail_records_errors():
    """两次子调用都重试失败 → 预判空 + errors 显著记录 + 置信度低。"""
    node = ForwardMappingNode(_AlwaysFailReasoner(), FakeStockMaster(), storage=None)
    out = node.run({
        "event": _event(), "patterns": [_pattern()], "scores": [0.82], "degradation_level": "strong",
    })
    assert len(out.errors) == 2
    assert any("受益" in e for e in out.errors)
    assert any("受损" in e for e in out.errors)
    assert out.beneficiary_predictions == []
    assert out.victim_predictions == []
    assert "推理失败" in out.confidence
