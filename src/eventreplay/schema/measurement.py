"""ImpactMeasurement：单标的×单子事件的量化度量（ImpactMeasurement 节点产出）。

注意（spec §4.3）：单标的不产出 CAR 的 t 值（对 σ 假设敏感、不可靠）；
显著性由经济显著性阈值 + 组合统计量（AttributionResult.pool_stats）表达。
"""

from pydantic import BaseModel, Field

from .common import StockDirection, Tier


class ImpactMeasurement(BaseModel):
    """标的级量化证据。"""

    stock_id: str
    stock_name: str | None = None  # 从 StockMaster.resolve 填入
    episode_id: str
    chain_node_id: str | None = None
    sw_industry_l3: str | None = None  # 申万三级行业（tushare 反向映射）/ 港股恒生行业
    board: str | None = None  # 标的板块（economic_threshold 按板块取阈值）
    car_pre_20: float | None = None  # 事件前 CAR[t-20,t-1]（预期差验证：大→已pricing in→"利好出尽"风险）
    direction: StockDirection | None = Field(default=None, description="来自 CandidateGeneration")
    direction_conflict: bool = Field(
        default=False, description="direction 与 CAR 符号冲突（spec §4.3 两段式）"
    )

    # 幅度（spec §4.4）
    car_0_1: float | None = None
    car_0_5: float | None = None
    car_0_20: float | None = None
    car_0_60: float | None = None
    car_0_90: float | None = None
    car_neg1_0: float | None = None  # [-1,0] 信息泄露诊断窗
    # 行业基准 CAR（v3.1 双层基准：剥离板块 β 后的个股 alpha）
    car_industry_0_20: float | None = None
    car_industry_0_60: float | None = None
    car_industry_0_90: float | None = None
    peak_ar: float | None = None
    max_drawdown: float | None = None
    limit_up_days: int = 0
    limit_down_days: int = 0

    # 持续性（spec §4.4；sig_ar_days 仅标的内时序，禁止跨标的排序）
    persistence_ratio_60_5: float | None = None
    sig_ar_days: int | None = None
    aav_days: int | None = None

    # 置信与分级
    low_confidence: bool = False
    low_confidence_reason: str | None = None
    tier: Tier | None = None
    rationale: str | None = None
