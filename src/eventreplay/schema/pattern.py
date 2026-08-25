"""Pattern / ArchetypalStock：规律库实体（Phase 2，具象化总计划 §2 Pattern）。"""

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from .common import ImpactDirection, ShockDimension


class ArchetypalStock(BaseModel):
    """典型标的（观察到的单值，非区间——区间待多 Replay 聚合，后续）。"""

    industry_node: str
    stocks: list[str] = Field(default_factory=list)
    observed_car_0_20: float | None = None
    observed_car_0_60: float | None = None
    persistence_note: str | None = None


class PatternQualitative(BaseModel):
    """事件级定性标签（影响程度/范围/持续时间/显著性），供前瞻对比参考。

    Literal 档位标签（LLM 非法值 → None，不虚构默认档位）；锚点是 LLM 推断依据非硬规则。
    """

    magnitude: Literal["极大", "大", "中", "小"] | None = None  # 影响程度：池|caar|>20%且n>10→极大...
    scope: Literal["广", "中", "窄"] | None = None  # 影响范围：池n>20或行业>5→广...
    persistence: Literal["长期", "中期", "短期脉冲"] | None = None  # 持续：ratio中位数>1.5→长期...
    significance: Literal["显著", "不显著"] | None = None  # grank_p<0.05→显著
    surprise: Literal["超预期", "符合预期", "利好出尽"] | None = None  # 市场定价：car_pre_20小+事件后CAR显著→超预期；car_pre_20大+反向→利好出尽
    rationale: str = ""
    pool_caar_beneficiary: float | None = None
    pool_caar_victim: float | None = None

    _VALID_LABELS: ClassVar[dict[str, set[str]]] = {
        "magnitude": {"极大", "大", "中", "小"},
        "scope": {"广", "中", "窄"},
        "persistence": {"长期", "中期", "短期脉冲"},
        "significance": {"显著", "不显著"},
        "surprise": {"超预期", "符合预期", "利好出尽"},
    }

    @field_validator("magnitude", "scope", "persistence", "significance", "surprise", mode="before")
    @classmethod
    def _invalid_label_to_none(cls, v, info):
        """LLM 非法档位 → None（与 shock_dimension 同口径：不虚构默认值）。"""
        return v if v in cls._VALID_LABELS[info.field_name] else None


class Pattern(BaseModel):
    """从一次 Replay 提炼的可复用规律模板。"""

    id: str
    source_replay_id: str  # UNIQUE：重跑 extract 覆盖
    created_at: datetime
    # 可追溯
    model_alias: str
    prompt_hash: str
    embedding_model: str | None = None  # "embedding-3"
    # 签名（检索键）。LLM 非法值 → None（不虚构默认桶：错桶=永远检索不到，宁缺毋滥）
    shock_dimension: ShockDimension | None = None
    impact_direction: ImpactDirection | None = None
    event_type: str  # 自由词表：仅显示
    industries: list[str] = Field(default_factory=list)  # 子串过滤
    event_summary: str  # embedding 输入
    embedding: list[float] | None = None  # dim 2048；null=嵌入失败
    # 传导模板
    transmission_template: str
    # 典型标的
    archetypal_beneficiaries: list[ArchetypalStock] = Field(default_factory=list)
    archetypal_victims: list[ArchetypalStock] = Field(default_factory=list)
    evidence_replay_ids: list[str] = Field(default_factory=list)  # MVP = [source_replay_id]
    # 事件级定性（增强；旧 Pattern 无此字段 → None，向后兼容）
    qualitative: PatternQualitative | None = None
