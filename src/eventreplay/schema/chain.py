"""TransmissionChain：产业链逻辑链（TransmissionChainSynthesis 产出，有向图）。"""

from pydantic import BaseModel, Field

from .common import StockDirection


class ChainNode(BaseModel):
    """产业链节点（上/中/下游，带申万行业与受冲击方向）。"""

    id: str
    label: str = Field(description="自由文本描述，如 '光模块'")
    sw_industry_l1: str | None = None
    level: str = Field(description="上/中/下游")
    shock_direction: StockDirection | None = None
    low_confidence: bool = False


class ChainEdge(BaseModel):
    """传导边（节点间因果关系）。"""

    src: str
    dst: str
    relation: str = Field(description="如 需求拉动/供给替代/成本传导")
    direction: StockDirection | None = None
    strength: str | None = Field(default=None, description="强/中/弱")


class TransmissionChain(BaseModel):
    """完整逻辑链。"""

    event_id: str
    nodes: list[ChainNode] = Field(default_factory=list)
    edges: list[ChainEdge] = Field(default_factory=list)
    beneficiary_stock_ids: list[str] = Field(default_factory=list)
    victim_stock_ids: list[str] = Field(default_factory=list)
    narrative: str | None = Field(default=None, description="逻辑链文字叙述（ReportAssembly 用）")

    @property
    def low_confidence_ratio(self) -> float:
        if not self.nodes:
            return 0.0
        return sum(1 for n in self.nodes if n.low_confidence) / len(self.nodes)
