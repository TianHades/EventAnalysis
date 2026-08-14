"""ReportAssembly：全部产物 → markdown 报告（spec §7）。"""

import json
from typing import Any

from pydantic import BaseModel

from .base import LLMNode


class _ReportOut(BaseModel):
    markdown: str


class ReportNode(LLMNode):
    node_id = "report_assembly"
    output_model = _ReportOut

    def system_prompt(self) -> str:
        return (
            "你是事件复盘分析师。报告的产业链传导/标的表/统计/判读指南/局限由系统模板生成，\n"
            "你**只需产出两节：## 1 事件摘要 + ## 4 逻辑链判读分析**（系统按分隔符拆分插入报告）。\n\n"
            "**输出格式（严格遵守）**：\n"
            "## 1 事件摘要\n（事件本质 + 市场主线 + 关键发现 3-5 句，聚焦洞察）\n\n"
            "===ANALYSIS===\n\n"
            "## 4 逻辑链判读分析\n（分 2-4 条主线，每条含因果判读+双层CAR证据+背离归因+预期差）\n\n"
            "**不要输出 ## 2/## 3/## 5 等其他章节，不要复述传导链（chain_narrative 已有）/标的表。**\n"
            "**总输出控制在 3000 字以内。**\n\n"
            "## 4 内容：因果判读（引用 chain_narrative，勿重写）/ 双层CAR证据 / 背离归因（事前可观察变量）/"
            "预期差（car_pre_20）/ 末尾'影响规模汇总'(pool_stats判大/中/小) + '事件影响定性'小节"
            "(5维各一行:影响程度极大/大/中/小|影响范围广/中/窄|持续时间长期/中期/短期脉冲|显著性显著/不显著|超预期性超预期/符合预期/利好出尽,各附依据。超预期性看 car_pre_20:绝对值<3%+事件后CAR显著→超预期,>10%+CAR反向→利好出尽)\n\n"
            "=== 双层CAR判读硬规则 ===\n"
            "CAR(市场)=相对沪深300总效应；CAR(行业)=相对同行业peers超额。\n"
            "• CAR(市场)>0 且 CAR(行业)>0 且|差|≤10pct → 个股独立α强\n"
            "• CAR(市场)<0 且 |CAR(行业)|>|CAR(市场)| → peer池落后标的（禁止说抗跌）\n\n"
            "=== 预期差（car_pre_20）===\n"
            "• car_pre_20>10% + 事件后CAR反向 → 利好出尽；≈0 + CAR显著 → 超预期\n\n"
            "=== 影响规模 ===\n"
            "大：池|caar|>10% 且>10只；中：5-10%；小：<5%\n\n"
            "直接输出 markdown（## 1 开头，===ANALYSIS=== 分隔，## 4 结尾）。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        e = inputs["event"]
        chain: Any = inputs["chain"]
        ar: Any = inputs["attribution_result"]
        measurements = inputs["measurements"]
        data = {
            "event": {
                "description": e.description,
                "announcement_date": str(e.announcement_date),
                "reaction_date": str(e.reaction_date),
                "type": e.event_type,
                "market": e.market,
                "benchmark": "HSI" if e.market == "港股" else "沪深300",
            },
            "chain_narrative": chain.narrative[:3000] if chain and chain.narrative else "",
            "beneficiary": [m.model_dump() for m in ar.beneficiary_pool],
            "victim": [m.model_dump() for m in ar.victim_pool],
            "pool_stats": ar.pool_stats if hasattr(ar, "pool_stats") else {},
            "measurements": [
                {
                    "stock_id": m.stock_id,
                    "stock_name": m.stock_name,
                    "sw_industry_l3": m.sw_industry_l3,
                    "car_pre_20": m.car_pre_20,  # 事件前CAR（预期差验证）
                    "car_0_1": m.car_0_1,
                    "car_0_5": m.car_0_5,
                    "car_0_20": m.car_0_20,
                    "car_0_60": m.car_0_60,
                    "car_0_90": m.car_0_90,
                    "car_industry_0_20": m.car_industry_0_20,
                    "car_industry_0_60": m.car_industry_0_60,
                    "car_industry_0_90": m.car_industry_0_90,
                    "persistence_ratio_60_5": m.persistence_ratio_60_5,
                    "tier": m.tier.value if m.tier else None,
                    "low_confidence": m.low_confidence,
                    "rationale": m.rationale,
                }
                for m in measurements
            ],
        }
        return f"结构化数据：\n{json.dumps(data, ensure_ascii=False, default=str)}"

    def prompt_hash(self) -> str:
        # text 模式无 schema 参与，只用 system_prompt 指纹（改 prompt 即失效缓存）
        import hashlib

        return hashlib.md5(self.system_prompt().encode()).hexdigest()

    def run(self, inputs: dict[str, Any]) -> str:
        """LLM 写 ## 1 摘要 + ## 4 判读（===ANALYSIS=== 分隔）→ 模板拼完整报告；失败纯模板兜底。"""
        from ...measurement.report_template import render_report

        try:
            full = self.reasoner.call_text(
                system=self.system_prompt(), user=self.build_user(inputs)
            )
            if "===ANALYSIS===" in full:
                summary_md, analysis_md = full.split("===ANALYSIS===", 1)
                summary_md, analysis_md = summary_md.strip(), analysis_md.strip()
            else:
                summary_md, analysis_md = full.strip(), None
            return render_report(
                event=inputs["event"],
                chain=inputs.get("chain"),
                attribution=inputs["attribution_result"],
                measurements=inputs["measurements"],
                summary_md=summary_md,
                analysis_md=analysis_md,
            )
        except Exception:
            return render_report(
                event=inputs["event"],
                chain=inputs.get("chain"),
                attribution=inputs["attribution_result"],
                measurements=inputs["measurements"],
            )
