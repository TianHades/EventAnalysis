"""CandidateGeneration：事件+产业链 → 候选标的清单（检索优先降级为 LLM 提议 + StockMaster 反查，spike §6）。"""

from typing import Any

from pydantic import BaseModel, Field

from ...adapters.stock_master import StockMaster
from ...schema import StockDirection
from .base import LLMNode

MAX_CANDIDATES = 40  # 候选池上限（LLM 提议优先保留；tushare/主题补充封顶，防止度量爆炸）
MAX_SOURCE_PER_NODE = 10  # 单节点成分股补充上限（防大行业误命中挤占其他节点名额）


class _CandOut(BaseModel):
    name: str
    direction: str
    chain_node_label: str
    rationale: str


class _CandsOut(BaseModel):
    candidates: list[_CandOut] = Field(default_factory=list)


class CandidateNode(LLMNode):
    node_id = "candidate_generation"
    output_model = _CandsOut

    def __init__(self, reasoner, stock_master: StockMaster, storage=None, constituent_sources: list | None = None) -> None:
        super().__init__(reasoner, storage)
        self.sm = stock_master
        self.sources = constituent_sources or []  # tushare 权威 + curated 兜底

    def system_prompt(self) -> str:
        return (
            "你是 A 股标的分析师。针对事件与产业链节点，提议受影响的真实 A 股标的（必须真实存在），"
            "每只给出：股票名称、方向（受益/受损）、所属产业链节点（与给定节点 label 对应）、影响逻辑。"
            "优先流动性好、代表性强的标的，每方向 3-5 只。只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        e = inputs["event"]
        nodes = inputs.get("chain_nodes", [])
        node_labels = "\n".join(f"- {n.label}（{n.sw_industry_l1}, {n.level}, {n.shock_direction}）" for n in nodes)
        market_hint = "港股标的（5位代码如00700）" if e.market == "港股" else "A股标的（6位代码）"
        return f"市场：{market_hint}\n事件：{e.description}（{e.event_type}）\n产业链节点：\n{node_labels}"

    def post_process(self, llm_output: _CandsOut, inputs: dict[str, Any]) -> list[dict]:
        nodes = inputs.get("chain_nodes", [])
        label_to_id = {n.label: n.id for n in nodes}

        def _match_node_label(lbl: str | None) -> str | None:
            """LLM 回显节点 label：精确优先，改写时双向包含兜底。"""
            if not lbl:
                return None
            if lbl in label_to_id:
                return label_to_id[lbl]
            for k, nid in label_to_id.items():
                if k in lbl or lbl in k:
                    return nid
            return None

        out: list[dict] = []
        for c in llm_output.candidates:
            resolved = self.sm.resolve(c.name)
            if resolved is None:
                continue  # 反幻觉：非真实标的剔除
            try:
                d = StockDirection(c.direction)
            except ValueError:
                continue
            out.append(
                {
                    "stock_id": resolved.code,
                    "name": resolved.name,
                    "chain_node_id": _match_node_label(c.chain_node_label),
                    "direction": d,
                    "rationale": c.rationale,
                }
            )

        # 成分股源补充（tushare 权威 + curated 兜底，治 LLM 召回遗漏如中船特气）
        # 港股跳过（A 股成分股源对港股无效，避免 A 股代码被 HKStockMaster 过滤）
        _evt = inputs.get("event")
        _market = getattr(_evt, "market", "A股") if _evt else "A股"
        if self.sources and _market != "港股":
            existing = {c["stock_id"] for c in out}
            for node in nodes:
                sd = node.shock_direction
                if sd is None:
                    continue
                text = f"{node.label} {node.sw_industry_l1 or ''}"
                added = 0
                for source in self.sources:
                    for hit in source.lookup(text):
                        if added >= MAX_SOURCE_PER_NODE:
                            break
                        code = hit["code"]
                        if code in existing:
                            continue
                        resolved = self.sm.resolve(code)
                        if resolved is None:
                            continue
                        out.append(
                            {
                                "stock_id": resolved.code,
                                "name": resolved.name,
                                "chain_node_id": node.id,
                                "direction": sd,
                                "rationale": f"[{hit.get('theme', '成分股源')}补充]",
                            }
                        )
                        existing.add(resolved.code)
                        added += 1
                    if added >= MAX_SOURCE_PER_NODE:
                        break
        # 规模上限：LLM 提议在前（已优先保留），超出 MAX 截断
        if len(out) > MAX_CANDIDATES:
            out = out[:MAX_CANDIDATES]
        return out
