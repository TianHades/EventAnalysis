"""Stock：标的主数据（StockMaster 维护）。"""

from datetime import date

from pydantic import BaseModel, Field

from .common import Board


class Stock(BaseModel):
    """A 股标的。含历史曾用名与申万行业归属，供反查与候选检索。"""

    code: str = Field(description="6 位代码，如 600519")
    name: str = Field(description="当前简称")
    market: str = Field(default="A股", description="市场")
    board: Board | None = None
    sw_industry_l1: str | None = Field(default=None, description="申万一级行业代码")
    sw_industry_l2: str | None = None
    sw_industry_l3: str | None = None
    hk_industry: str | None = Field(default=None, description="港股恒生行业（港股用）")
    listing_date: date | None = None
    is_st: bool = Field(default=False, description="事件期当时是否 ST（非当前状态）")
    historical_names: list[str] = Field(default_factory=list, description="历史曾用名")
