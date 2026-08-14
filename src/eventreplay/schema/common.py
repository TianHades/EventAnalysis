"""跨实体共享的枚举与基础类型。"""

from enum import Enum


class AnnouncementTime(str, Enum):
    """公告时点（决定 reaction_date 归属，见 spec §4.1）。"""

    PRE_OPEN = "盘前"  # <09:30 → t0=当日
    INTRADAY = "盘中"  # 09:30–15:00 → t0=当日但 AR[t0] 仅含部分
    AFTER_CLOSE = "盘后"  # >15:00 → t0=下一交易日
    NON_TRADING = "非交易日"  # → 取下一交易日


class ShockDimension(str, Enum):
    """事件冲击维度。"""

    SUPPLY = "供给"
    DEMAND = "需求"
    POLICY = "政策"
    LIQUIDITY = "流动性"
    SENTIMENT = "情绪"


class ImpactDirection(str, Enum):
    """事件整体影响方向。"""

    POSITIVE = "利好"
    NEGATIVE = "利空"
    NEUTRAL = "中性"
    MIXED = "混合"


class StockDirection(str, Enum):
    """单标的预期方向（CandidateGeneration 产出，AttributionFiltering 校验）。"""

    BENEFICIARY = "受益"
    VICTIM = "受损"


class Tier(str, Enum):
    """强弱分级（AttributionFiltering 产出）。"""

    STRONG = "强"
    MEDIUM = "中"
    WEAK = "弱"
    UNGRADED = "未分级"
    LOW_CONFIDENCE = "低置信"


class Board(str, Enum):
    """A 股板块（涨跌停规则与新股规则按此查表，见 spec §4.4）+ 港股板块。"""

    MAIN = "主板"
    CREATIVE = "创业板"
    STAR = "科创板"
    BSE = "北交所"
    HK_MAIN = "港股主板"  # 港股主板（无涨跌停）


class IndustryGranularity(str, Enum):
    """申万行业成分股粒度（受数据可得性约束，spike：仅一级稳定可用）。"""

    L1 = "一级"
    L2 = "二级"
    L3 = "三级"
