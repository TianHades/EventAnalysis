"""PatternExtractor：LLM 读 Replay 上下文 → 结构化 Pattern（Phase 2 规律提炼）。"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ...llm import Embedder
from ...schema import ArchetypalStock, ImpactDirection, Pattern, PatternQualitative, ShockDimension
from .base import LLMNode


class _ArchetypalStockOut(BaseModel):
    industry_node: str
    stocks: list[str] = Field(default_factory=list)
    observed_car_0_20: float | None = None
    observed_car_0_60: float | None = None
    persistence_note: str | None = None


class _PatternLLM(BaseModel):
    shock_dimension: str  # 供给/需求/政策/流动性/情绪
    impact_direction: str  # 利好/利空/中性/混合
    event_type: str
    industries: list[str] = Field(default_factory=list)
    event_summary: str = Field(description="≤200 字标准化摘要，embedding 输入")
    transmission_template: str
    archetypal_beneficiaries: list[_ArchetypalStockOut] = Field(default_factory=list)
    archetypal_victims: list[_ArchetypalStockOut] = Field(default_factory=list)
    qualitative: PatternQualitative | None = None  # 事件级定性（程度/范围/持续/显著）


def _to_replay_json(replay_ctx: dict[str, Any]) -> str:
    e = replay_ctx["event"]
    ar = replay_ctx.get("attribution_result")
    ms = replay_ctx.get("measurements", [])
    pool_stats = (getattr(ar, "pool_stats", None) or {}) if ar else {}
    data = {
        "event": e.model_dump(mode="json"),
        "beneficiary": [m.model_dump(mode="json") for m in (ar.beneficiary_pool if ar else [])],
        "victim": [m.model_dump(mode="json") for m in (ar.victim_pool if ar else [])],
        # 结构化 JSON（而非 Python repr：LLM 从 repr 读 p 值易错读）
        "pool_stats": {k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in pool_stats.items()},
        "measurements": [
            {
                "stock_id": m.stock_id,
                "car_0_20": m.car_0_20,
                "car_0_60": m.car_0_60,
                "car_pre_20": m.car_pre_20,
                "persistence_ratio_60_5": m.persistence_ratio_60_5,
                "tier": m.tier.value if m.tier else None,
            }
            for m in ms
        ],
    }
    # 事件级聚合 hint（避免 LLM 自己数）：按池算 persistence 中位数/STRONG 占比 + 传导节点数
    ben_ids = {m.stock_id for m in (ar.beneficiary_pool if ar else [])}
    vic_ids = {m.stock_id for m in (ar.victim_pool if ar else [])}

    def _pool_agg(pool_ms: list) -> dict:
        ratios = [m.persistence_ratio_60_5 for m in pool_ms if m.persistence_ratio_60_5 is not None]
        n = len(pool_ms)
        return {
            "persistence_ratio_median": round(statistics.median(ratios), 2) if ratios else None,
            "strong_ratio": round(sum(1 for m in pool_ms if m.tier and m.tier.value == "强") / n, 2) if n else None,
            "n": n,
        }

    chain = replay_ctx.get("chain")
    data["event_aggregates"] = {
        "beneficiary": _pool_agg([m for m in ms if m.stock_id in ben_ids]),
        "victim": _pool_agg([m for m in ms if m.stock_id in vic_ids]),
        "chain_node_count": len(chain.nodes) if chain and getattr(chain, "nodes", None) else 0,
    }
    return json.dumps(data, ensure_ascii=False, default=str)


class PatternExtractor(LLMNode):
    """读 Replay 上下文 → _PatternLLM → Pattern（post_process 调 Embedder）。"""

    node_id = "pattern_extract"
    output_model = _PatternLLM
    output_type = Pattern  # SQLite 缓存重建

    def __init__(self, reasoner, embedder: Embedder, storage=None) -> None:
        super().__init__(reasoner, storage)
        self.embedder = embedder

    def system_prompt(self) -> str:
        return (
            "你是 A 股事件复盘规律提炼师。基于一个完整复盘（事件+受益/受损标的+量化度量），"
            "提炼可复用规律模板：① 事件签名（冲击维度 shock_dimension 必须取：供给/需求/政策/流动性/情绪；"
            "影响方向 impact_direction 必须取：利好/利空/中性/混合；事件类型/受影响行业）；"
            "② ≤200 字标准化事件摘要；③ 传导模板（事件→产业链→标的 的因果链）；"
            "④ 典型受益/受损标的（stocks 必须用 6 位股票代码，照抄 measurements 的 stock_id，"
            "不要用名称——系统按代码对账回填；其 observed_car 由系统按真实度量核对回填："
            "有度量的标的取度量真值、无度量的置空，LLM 数值不采信——无需精确估计 CAR）；"
            "⑤ 事件级定性 qualitative 4 维——"
            "magnitude 影响程度(极大/大/中/小:池|caar|>20%且n>10→极大,10-20%→大,5-10%→中,<5%→小)；"
            "scope 影响范围(广/中/窄:池n>20或行业>5→广,10-20→中,<10→窄)；"
            "persistence 持续时间(长期/中期/短期脉冲:event_aggregates的persistence_ratio_median>1.5→长期,0.7-1.5→中期,<0.7→短期)；"
            "significance 显著性(显著/不显著:pool_stats的p值<0.05→显著；注意 grank_p 字段当前装的是横截面CAR[0,+20]简化t检验的p值)；"
            "surprise 超预期性(超预期/符合预期/利好出尽:measurements的car_pre_20绝对值<3%+事件后CAR显著→超预期,car_pre_20>10%+CAR反向→利好出尽)；"
            "qualitative.rationale 给一句综合依据；qualitative.pool_caar_beneficiary/victim "
            "由系统按 pool_stats 真值回填（与④同理不采信 LLM 数值），可留空。"
            "只输出 JSON。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        return f"复盘数据：\n{_to_replay_json(inputs['replay_context'])}"

    def post_process(self, llm: _PatternLLM, inputs: dict[str, Any]) -> Pattern:
        source_replay_id = inputs["source_replay_id"]
        replay_ctx: dict[str, Any] = inputs.get("replay_context") or {}
        measurements: list[Any] = replay_ctx.get("measurements") or []
        ar = replay_ctx.get("attribution_result")

        # 数值对账：LLM 抄录的 CAR 一律不采信——有度量则回填真值，无度量置 None
        # （否则幻觉值会成为前瞻"预期 CAR"来源贯穿全链）。
        # 索引按代码优先、名称兜底：LLM 即使违背 prompt 用了名称也能对上
        m_by_key: dict[str, Any] = {m.stock_id: m for m in measurements}
        for m in measurements:
            name = getattr(m, "stock_name", None)
            if name:
                m_by_key.setdefault(name, m)  # 重名时不覆盖代码命中的主索引

        def _reconcile(stocks: list[_ArchetypalStockOut]) -> list[ArchetypalStock]:
            out: list[ArchetypalStock] = []
            for s in stocks:
                # 组内多个标的都有度量时取 |car_0_20| 最大者为代表
                # （"典型标的"本意即反应最强者，按 LLM 列出顺序取首个是任意行为）
                hits = [m_by_key[x] for x in s.stocks if x in m_by_key]
                rep = max(hits, key=lambda m: abs(m.car_0_20 or 0.0), default=None)
                out.append(
                    ArchetypalStock(
                        industry_node=s.industry_node,
                        stocks=s.stocks,
                        observed_car_0_20=rep.car_0_20 if rep else None,
                        observed_car_0_60=rep.car_0_60 if rep else None,
                        persistence_note=s.persistence_note,
                    )
                )
            return out

        # 池 caar 同样对账：只从 replay_context 的 pool_stats 回填
        pool_stats = (getattr(ar, "pool_stats", None) or {}) if ar else {}

        def _pool_caar(key: str) -> float | None:
            stat = pool_stats.get(key)
            return getattr(stat, "caar", None)

        qualitative = llm.qualitative
        if qualitative is not None:
            qualitative = qualitative.model_copy(
                update={
                    "pool_caar_beneficiary": _pool_caar("beneficiary"),
                    "pool_caar_victim": _pool_caar("victim"),
                }
            )

        # 非法枚举 → None（shock_dimension 是检索硬过滤键，虚构默认桶=永远检索不到）
        def _safe_enum(enum_cls, value):
            try:
                return enum_cls(value)
            except ValueError:
                return None

        sd = _safe_enum(ShockDimension, llm.shock_dimension)
        idir = _safe_enum(ImpactDirection, llm.impact_direction)

        embedding = self.embedder.embed(llm.event_summary)
        if embedding is None:
            print(
                f"[pattern] embedding 失败 pat-{source_replay_id} "
                "将对向量检索不可见（可跑 spikes/reembed_patterns.py 补救）"
            )
        return Pattern(
            id=f"pat-{source_replay_id}",
            source_replay_id=source_replay_id,
            created_at=datetime.utcnow(),
            model_alias=self.model_alias(),
            prompt_hash=self.prompt_hash(),
            embedding_model=self.embedder.model_name if embedding else None,
            shock_dimension=sd,
            impact_direction=idir,
            event_type=llm.event_type,
            industries=llm.industries,
            event_summary=llm.event_summary,
            embedding=embedding,
            transmission_template=llm.transmission_template,
            archetypal_beneficiaries=_reconcile(llm.archetypal_beneficiaries),
            archetypal_victims=_reconcile(llm.archetypal_victims),
            evidence_replay_ids=[source_replay_id],
            qualitative=qualitative,
        )
