"""AttributionResult：AttributionFiltering 节点输出 → TransmissionChainSynthesis 输入契约（spec §3.3）。"""

from typing import Literal

from pydantic import BaseModel, Field

from .common import IndustryGranularity, StockDirection, Tier


class PoolMember(BaseModel):
    """受益/受损池成员。"""

    stock_id: str
    stock_name: str | None = None  # 从度量侧带出（下游 synthesis/report 免反查）
    chain_node_id: str | None = None
    direction: StockDirection
    tier: Tier = Tier.UNGRADED
    episode_id: str
    low_confidence: bool = False
    direction_conflict: bool = False
    rationale: str | None = None


class PoolStat(BaseModel):
    """单池组合统计量（GRANK + Adjusted BMP，spec §4.3）。N<5 时 disclosed=False。"""

    n: int = Field(description="进入统计的有效样本数（car_0_20 缺失/WEAK fallback 成员不计入）")
    caar: float | None = None
    grank_z: float | None = None
    grank_p: float | None = None
    adj_bmp_z: float | None = None
    adj_bmp_p: float | None = None
    disclosed: bool = Field(default=True, description="N<5 时仅记录不披露")
    disagreement: bool = Field(default=False, description="GRANK 与 Adjusted BMP 结论分歧")
    fallback_n: int = Field(default=0, description="WEAK fallback 列示成员数（不进 caar/t 检验）")


class AttributionResult(BaseModel):
    """节点5输出。受益池/受损池成员 + 池级统计量（仅披露，不作过滤输入）。"""

    beneficiary_pool: list[PoolMember] = Field(default_factory=list)
    victim_pool: list[PoolMember] = Field(default_factory=list)
    pool_stats: dict[Literal["beneficiary", "victim"], PoolStat] = Field(default_factory=dict)
    tier_definitions: dict[str, str] = Field(default_factory=dict, description="强/中/弱 阈值说明")
    industry_granularity: IndustryGranularity = IndustryGranularity.L1

    def pool_of(self, direction: StockDirection) -> list[PoolMember]:
        return self.beneficiary_pool if direction == StockDirection.BENEFICIARY else self.victim_pool
