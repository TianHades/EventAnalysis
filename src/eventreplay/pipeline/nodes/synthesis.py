"""TransmissionChainSynthesis：产物 → 逻辑链（mermaid + 叙述）。"""

import json
from typing import Any

from pydantic import BaseModel

from ...schema import TransmissionChain
from .base import LLMNode


class _SynthOut(BaseModel):
    mermaid: str
    narrative: str


class TransmissionChainNode(LLMNode):
    node_id = "transmission_chain"
    output_model = _SynthOut
    output_type = TransmissionChain  # SQLite 缓存重建

    def system_prompt(self) -> str:
        return (
            "你是产业链逻辑链分析师。基于事件、产业链节点、受益/受损标的，**逐步推导**传导逻辑链：\n"
            "① 事件本质（发生了什么、为什么对资本市场重要）\n"
            "② 直接冲击（第一层受影响的产业环节 + 方向 + 因果机制）\n"
            "③ 间接传导（第二/三层传导路径：替代/互补/成本/需求传导，每步说明因果）\n"
            "④ 标的映射（每个节点→哪些标的、为什么这些标的最受益/受损、竞争格局变化）\n"
            "⑤ 预期差（哪些标的逻辑方向与实际 CAR 背离、推测原因：估值/持仓/传导滞后/风格）\n"
            "产出 mermaid 有向图（边标注传导机制如'需求拉动'/'成本挤压'）+ 分步文字叙述。只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        e = inputs["event"]
        nodes = inputs["chain_nodes"]
        ar: Any = inputs["attribution_result"]
        data = {
            "event": e.description,
            "nodes": [
                {"label": n.label, "industry": n.sw_industry_l1, "shock": n.shock_direction}
                for n in nodes
            ],
            # code+name 成对传（裸 6 位代码易致 LLM 背错公司名张冠李戴）
            "beneficiary": [
                {"code": m.stock_id, "name": m.stock_name} for m in ar.beneficiary_pool
            ],
            "victim": [
                {"code": m.stock_id, "name": m.stock_name} for m in ar.victim_pool
            ],
        }
        return f"结构化数据：\n{json.dumps(data, ensure_ascii=False)}"

    def post_process(self, llm_output: _SynthOut, inputs: dict[str, Any]) -> TransmissionChain:
        ar = inputs["attribution_result"]
        return TransmissionChain(
            event_id=inputs["event"].id,
            nodes=inputs["chain_nodes"],
            beneficiary_stock_ids=[m.stock_id for m in ar.beneficiary_pool],
            victim_stock_ids=[m.stock_id for m in ar.victim_pool],
            narrative=f"```mermaid\n{llm_output.mermaid}\n```\n\n{llm_output.narrative}",
        )
