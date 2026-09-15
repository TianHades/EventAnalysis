"""ForwardResult / ForwardPrediction：前瞻映射产物（Phase 3）。"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from .common import StockDirection
from .event import Event
from .chain import ChainNode
from .pattern import Pattern, QUALITATIVE_LABELS

CalibrationStatus = Literal[
    "confirming",  # 实际 CAR 达预期方向 50% 达标线
    "diverging",   # 实际 CAR 与预期方向相反
    "neutral",     # 方向一致但未达标线（真实走平）
    "pending",     # 数据窗不足（t0 当天 / 度量不可得）
    "no_range",    # 纯定性预判（无预期区间，无从校准）
    "parse_error", # 预期区间字符串解析失败
]


class ForwardPrediction(BaseModel):
    """单标的前瞻预判。"""

    stock_id: str
    stock_name: str | None = None
    direction: StockDirection
    expected_car_0_20_range: str | None = None  # "10%~30%"（纯定性模式为 None）
    expected_car_0_60_range: str | None = None
    rationale: str
    source_pattern_ids: list[str] = Field(default_factory=list)
    # 盘后校准（仅 post 模式 + strong 层）
    actual_car_0_1: float | None = None
    actual_car_0_5: float | None = None
    calibration: CalibrationStatus | None = None


class QualitativeForecast(BaseModel):
    """事件五维定性预判（事前，LLM 综合程序锚点产出；事后与 PatternQualitative 对账）。

    档位 Literal 与 PatternQualitative 同口径（LLM 非法值 → None，不虚构默认档位）。
    dim_confidence 是模糊判断的元信息：事后对账中"低置信度判对/高置信度判错"最有信息量。
    """

    magnitude: Literal["极大", "大", "中", "小"] | None = None
    scope: Literal["广", "中", "窄"] | None = None
    persistence: Literal["长期", "中期", "短期脉冲"] | None = None
    significance: Literal["显著", "不显著"] | None = None
    surprise: Literal["超预期", "符合预期", "利好出尽"] | None = None
    dim_confidence: dict[str, str] = Field(default_factory=dict)  # 维度名 → 高/中/低
    rationale: str = ""
    anchors_used: list[str] = Field(default_factory=list)  # 程序注入的客观锚点（可追溯）
    source: Literal["llm", "fallback"] = "llm"

    _VALID_LABELS: ClassVar[dict[str, set[str]]] = QUALITATIVE_LABELS  # 单一事实源（schema/pattern.py）
    _VALID_CONFIDENCE: ClassVar[set[str]] = {"高", "中", "低"}

    @field_validator("magnitude", "scope", "persistence", "significance", "surprise", mode="before")
    @classmethod
    def _invalid_label_to_none(cls, v, info):
        """LLM 非法档位 → None（与 PatternQualitative 同口径：不虚构默认值）。"""
        return v if v in cls._VALID_LABELS[info.field_name] else None

    @field_validator("dim_confidence", mode="before")
    @classmethod
    def _filter_confidence(cls, v):
        """非五维键或非法档位（非 高/中/低）的条目丢弃（节点已过滤，此为缓存重建/外部构造兜底）。"""
        if not isinstance(v, dict):
            return {}
        return {
            k: conf for k, conf in v.items()
            if k in cls._VALID_LABELS and conf in cls._VALID_CONFIDENCE
        }


class QualitativeCalibration(BaseModel):
    """五维定性对账结果（事前 QualitativeForecast vs 事后 PatternQualitative）。"""

    dim_results: dict[str, str] = Field(default_factory=dict)  # 维度 → 一致/不一致/预判缺失/实测缺失
    hit_rate: float | None = None  # 一致数 / 可对账维数（无可对账 → None）
    notes: str = ""


class ForwardResult(BaseModel):
    """前瞻映射完整结果。"""

    event_id: str
    event: Event
    mode: str = "pre"  # "pre" | "post"
    degradation_level: str = "qualitative"  # "strong" | "weak" | "qualitative"
    matched_pattern_ids: list[str] = Field(default_factory=list)
    matched_scores: list[float] = Field(default_factory=list)
    matched_patterns: list[Pattern] = Field(default_factory=list)  # 供 _generate_report 展示历史定性
    transmission_logic: str = ""
    beneficiary_predictions: list[ForwardPrediction] = Field(default_factory=list)
    victim_predictions: list[ForwardPrediction] = Field(default_factory=list)
    confidence: str = ""  # 高/中/低
    key_risks: str = ""
    chain_nodes: list[ChainNode] = Field(default_factory=list)  # 纯定性模式
    mapping_errors: list[str] = Field(default_factory=list)  # forward_mapping 子调用失败信息
    # 事件五维定性预判（None=节点失败/旧结果无此字段，向后兼容）
    qualitative_forecast: QualitativeForecast | None = None
    qualitative_forecast_error: str = ""  # 预判节点异常摘要（forecast=None 时报告展示原因）
