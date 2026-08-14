"""IndustryChainReasoning：事件 → 产业链节点图（每节点 sw_industry + 语义护栏，spec §3.1）。"""

from typing import Any

from pydantic import BaseModel, Field

from ...schema import ChainNode, StockDirection
from .base import LLMNode


class _ChainNodeOut(BaseModel):
    label: str
    sw_industry: str = Field(description="申万一级行业名称，如 农林牧渔/电子/计算机")
    level: str = Field(description="上游/中游/下游")
    shock_direction: str


class _ChainOut(BaseModel):
    nodes: list[_ChainNodeOut]


# 申万 2021 一级行业全名录：LLM 给出的 industry 命中即视为规范映射，无需词面校验
_SW_L1_NAMES = frozenset({
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "家用电器", "食品饮料",
    "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输", "房地产",
    "商贸零售", "社会服务", "综合", "建筑材料", "建筑装饰", "电力设备",
    "国防军工", "计算机", "传媒", "通信", "银行", "非银金融", "汽车",
    "机械设备", "环保", "美容护理", "煤炭", "石油石化",
})


def _semantic_ok(label: str, industry: str) -> bool:
    """轻量语义护栏：label 与行业名至少 1 个核心 token 重叠（spec §3.1，MVP 关键词法）。

    industry 已是规范申万一级行业名时直接放行（词面无关但映射正确的常见情形，
    如 光伏组件→电力设备、锂电材料→电力设备）。jieba 为可选依赖，缺失时退化。
    """
    if industry.strip() in _SW_L1_NAMES:
        return True
    try:
        import jieba  # 可选依赖（未声明时不得崩溃）

        lt = set(w for w in jieba.lcut(label) if len(w) >= 2)
        it = set(w for w in jieba.lcut(industry) if len(w) >= 2)
    except Exception:
        # 无 jieba 时退化为字符 n-gram 重叠
        lt = {label[i : i + 2] for i in range(len(label) - 1)}
        it = {industry[i : i + 2] for i in range(len(industry) - 1)}
    return bool(lt & it) or bool(set(label) & set(industry))


class IndustryChainNode(LLMNode):
    node_id = "industry_chain"
    output_model = _ChainOut
    output_type = list[ChainNode]  # SQLite 缓存重建

    def system_prompt(self) -> str:
        return (
            "你是 A 股产业链分析师。对给定事件，推理受冲击的产业链节点（上游/中游/下游），"
            "每节点给出自由文本描述、对应的申万一级行业名称、受冲击方向（受益/受损）。"
            "输出 3-8 个节点。只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        e = inputs["event"]
        market_hint = "港股（恒生行业分类）" if e.market == "港股" else "A股（申万一级行业）"
        return f"市场：{market_hint}\n事件：{e.description}\n类型：{e.event_type}\n影响方向：{e.impact_direction}"

    def post_process(self, llm_output: _ChainOut, inputs: dict[str, Any]) -> list[ChainNode]:
        out: list[ChainNode] = []
        for i, n in enumerate(llm_output.nodes):
            try:
                sd = StockDirection(n.shock_direction)
            except ValueError:
                sd = None
            low = not _semantic_ok(n.label, n.sw_industry)
            out.append(
                ChainNode(
                    id=f"cn-{i}",
                    label=n.label,
                    sw_industry_l1=n.sw_industry,
                    level=n.level,
                    shock_direction=sd,
                    low_confidence=low,
                )
            )
        return out
