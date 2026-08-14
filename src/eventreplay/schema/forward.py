"""ForwardResult / ForwardPrediction：前瞻映射产物（Phase 3）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import StockDirection
from .event import Event
from .chain import ChainNode
from .pattern import Pattern


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
    calibration: str | None = None  # confirming / diverging / neutral


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
