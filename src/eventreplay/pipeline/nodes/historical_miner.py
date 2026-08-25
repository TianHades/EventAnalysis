"""HistoricalEventMiner：日期范围 → LLM 列候选 + 代码硬过滤 + LLM 评估选取（多阶段，可追溯）。

三阶段：
  阶段1（LLM）：列 raw_top 候选（广撒网，含打分+理由）→ mining_raw_{job}.json
  阶段1.5（代码）：sig<2 或 a_share<3 硬过滤（仅剔明显噪音）→ mining_filtered_{job}.json
  阶段2（LLM）：评估选 top_n（重要性优先+同类去重+软多样性，每个附理由）→ mining_candidates_{job}.json
筛选标准：重要性优先 + 同类去重（语义，LLM 判断）+ 软多样性（不强制维度覆盖，同维度多个重要可都选）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from ...llm import Reasoner
from ...schema import AnnouncementTime, CandidateEvent, Event, ImpactDirection, ShockDimension
from ..event_dedup import make_dedup_key


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


class _MinedEventOut(BaseModel):
    description: str
    event_type: str
    shock_dimension: str | None = None
    impact_direction: str | None = None
    announcement_date: str  # YYYY-MM-DD
    announcement_time: Literal["盘前", "盘中", "盘后", "非交易日"] | None = None
    key_entities: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    significance: int  # 1-4
    a_share_relevance: int  # 1-5
    surprise_timing: int = 3  # 1-5：1=完全预期内，5=完全突发
    surprise_magnitude: int = 3  # 1-5：1=符合预期，5=远超预期
    impact_scale: int = 3  # 1-5：预估影响幅度（5=极大>20%，1=微<5%）
    rationale: str = ""  # 为什么值得复盘（初步理由）


class _EventMineOut(BaseModel):
    events: list[_MinedEventOut] = Field(default_factory=list)


class _SelectItemOut(BaseModel):
    index: int  # 候选在 kept 列表的索引
    reason: str  # 选取/剔除理由


class _EventSelectOut(BaseModel):
    selected: list[_SelectItemOut] = Field(default_factory=list)
    rejected: list[_SelectItemOut] = Field(default_factory=list)


def _mine_system(market: str = "A股") -> str:
    """挖掘 system prompt：市场名动态注入（港股挖掘时评分口径一致）。"""
    return (
        f"你是{market}事件研究分析师。列出给定日期范围内值得复盘的候选{market}相关事件（广撒网）。\n\n"
        "要求：\n"
        "① 列出尽可能多的候选（含各级重要性，供后续筛选，不要预先过度筛选）\n"
        "② 重大性打分（1-4）：4=系统级（冲击全市场）；3=主线级（引领主线、多板块）；2=行业级；1=主题/噪音\n"
        f"③ 可达性（1-5）：是否有明确可映射的{market}标的\n"
        "④ 突发性（1-5）：1=完全预期内（预告/渐进/日程已知），5=完全突发（无预警）\n"
        "⑤ 超预期性（1-5）：1=符合市场共识预期，5=远超预期（力度/范围/时点超预期）\n"
        "⑥ 影响规模（1-5）：预估相关标的CAR幅度，5=极大(>20%)，4=大(10-20%)，3=中(5-10%)，2=小(<5%)，1=微\n"
        "⑦ 每个事件含：description / event_type / shock_dimension(供给/需求/政策/流动性/情绪) / "
        "impact_direction(利好/利空/中性/混合) / announcement_date(YYYY-MM-DD) / "
        "announcement_time(盘前/盘中/盘后/非交易日) / key_entities / industries / rationale(为什么值得复盘)\n"
        "只输出 JSON。"
    )

_SELECT_SYSTEM = (
    "你是 A 股事件复盘选材师。给定候选事件列表，选出最值得复盘的 top_n 个。\n\n"
    "标准：\n"
    "① 重要性优先：sig/a_share 高的优先\n"
    "② 同类去重：语义同类选更代表性的（如多个AI事件选最代表的）；同维度不同事件可都选（如注册制+降准同政策类但都重要，都保留）\n"
    "③ 软多样性：避免完全扎堆，但不强制覆盖所有维度，不为维度牺牲重要性\n"
    "④ 突发性+超预期性优先：surprise_timing/surprise_magnitude 高的优先（预期内事件可能'利好出尽'，超预期事件才有超额收益机会）\n"
    "⑤ 影响规模优先：impact_scale 高的优先（影响幅度大的事件更有复盘/前瞻价值）\n"
    "⑥ 每个选取/剔除给具体理由（引用事件特征）\n"
    "输出 selected（选取，含 index+理由）+ rejected（剔除，含 index+理由）。只输出 JSON。"
)


def _build_event(out: _MinedEventOut) -> Event | None:
    """日期不可解析 → None（调用方丢弃，不虚构 today()）。"""

    def _parse(d: str | None) -> date | None:
        if not d:
            return None
        try:
            return date.fromisoformat(str(d)[:10])
        except ValueError:
            return None

    ann = _parse(out.announcement_date)
    if ann is None:
        return None
    # 盘后/非交易日公告 → 首个反应日为公告日次日（event_study t0 对齐再推到下一交易日）
    react = ann + timedelta(days=1) if out.announcement_time in ("盘后", "非交易日") else ann
    try:
        at = AnnouncementTime(out.announcement_time) if out.announcement_time else None
    except ValueError:
        at = None
    try:
        sd = ShockDimension(out.shock_dimension) if out.shock_dimension else None
    except ValueError:
        sd = None
    try:
        idir = ImpactDirection(out.impact_direction) if out.impact_direction else None
    except ValueError:
        idir = None
    eid = f"evt-{hashlib.md5(out.description.encode('utf-8')).hexdigest()[:12]}"
    return Event(
        id=eid, description=out.description, announcement_date=ann, reaction_date=react,
        announcement_time=at,
        event_type=out.event_type, shock_dimension=sd, impact_direction=idir,
        key_entities=out.key_entities,
    )


def _mine_out_to_dict(e: _MinedEventOut) -> dict:
    return {
        "description": e.description, "event_type": e.event_type,
        "shock_dimension": e.shock_dimension, "impact_direction": e.impact_direction,
        "announcement_date": e.announcement_date, "announcement_time": e.announcement_time,
        "key_entities": e.key_entities,
        "industries": e.industries,
        "significance": _clamp(e.significance, 1, 4),
        "a_share_relevance": _clamp(e.a_share_relevance, 1, 5),
        "surprise_timing": _clamp(e.surprise_timing, 1, 5),
        "surprise_magnitude": _clamp(e.surprise_magnitude, 1, 5),
        "impact_scale": _clamp(e.impact_scale, 1, 5),
        "rationale": e.rationale,
    }


def _candidate_to_dict(c: CandidateEvent) -> dict:
    at = c.event.announcement_time
    return {
        "dedup_key": c.dedup_key, "description": c.event.description,
        "event_type": c.event.event_type,
        "shock_dimension": c.event.shock_dimension.value if c.event.shock_dimension else None,
        "impact_direction": c.event.impact_direction.value if c.event.impact_direction else None,
        "announcement_date": str(c.event.announcement_date),
        "announcement_time": at.value if at else None,
        "key_entities": c.event.key_entities, "industries": c.industries,
        "significance": c.significance, "a_share_relevance": c.a_share_relevance,
        "surprise_timing": c.surprise_timing, "surprise_magnitude": c.surprise_magnitude,
        "impact_scale": c.impact_scale,
        "source": c.source,
    }


class HistoricalEventMiner:
    """多阶段挖掘：LLM 列候选 → 代码硬过滤 → LLM 评估选取，每阶段落盘可追溯。"""

    def __init__(self, reasoner: Reasoner, storage=None) -> None:
        self.reasoner = reasoner
        self.storage = storage

    def mine(
        self,
        *,
        start_date: str,
        end_date: str,
        top_n: int = 5,
        industry: str | None = None,
        shock_dimension: str | None = None,
        raw_top: int = 20,
        market: str = "A股",
        out_dir: str | Path = "data",
        echo: Callable[[str], None] = print,
    ) -> dict:
        """三阶段挖掘，返回 {final, raw_count, filtered_out, llm_rejected, paths}。"""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"{start_date}_{end_date}"

        # 阶段1：LLM 列候选
        echo(f"[mine] 阶段1：LLM 列候选（top {raw_top}, market={market}）...")
        try:
            raw = self._mine_candidates(start_date, end_date, industry, shock_dimension, raw_top, market)
        except Exception as ex:
            echo(f"[mine] 阶段1 LLM 失败: {type(ex).__name__}: {str(ex)[:80]}")
            return {"final": [], "raw_count": 0, "filtered_out": 0, "llm_rejected": 0, "paths": {}}
        echo(f"[mine] 阶段1 完成：{len(raw)} 候选")
        raw_path = _write_json(
            [_mine_out_to_dict(e) for e in raw], out_dir / f"mining_raw_{job_id}.json",
        )

        # 阶段1.5：代码硬过滤（sig/a_share + 日期可解析/范围内）
        kept, dropped = self._hard_filter(raw, start_date, end_date)
        echo(f"[mine] 阶段1.5 硬过滤：{len(raw)}→{len(kept)}（剔{len(dropped)}）")
        filtered_path = _write_json(
            {"kept": [_mine_out_to_dict(e) for e in kept], "dropped": dropped},
            out_dir / f"mining_filtered_{job_id}.json",
        )

        # 阶段2：LLM 评估选取
        echo(f"[mine] 阶段2：LLM 评估选取 top {top_n}...")
        try:
            selected, rejected = self._llm_select(kept, top_n)
        except Exception as ex:
            echo(f"[mine] 阶段2 LLM 失败: {type(ex).__name__}: {str(ex)[:80]}")
            selected, rejected = [], []
        echo(f"[mine] 阶段2 完成：选 {len(selected)}，剔 {len(rejected)}")
        final_path = _write_json(
            {
                "final": [_candidate_to_dict(c) for c in selected],
                "llm_rejected": rejected,
                "process_summary": {
                    "raw_count": len(raw), "filtered_out": len(dropped),
                    "llm_rejected": len(rejected), "final": len(selected),
                },
            },
            out_dir / f"mining_candidates_{job_id}.json",
        )

        return {
            "final": selected,
            "raw_count": len(raw),
            "filtered_out": len(dropped),
            "llm_rejected": len(rejected),
            "paths": {"raw": str(raw_path), "filtered": str(filtered_path), "final": str(final_path)},
        }

    def _mine_candidates(
        self, start: str, end: str, industry: str | None, shock: str | None, raw_top: int,
        market: str = "A股",
    ) -> list[_MinedEventOut]:
        filters = [f"市场={market}"]
        if industry:
            filters.append(f"行业={industry}")
        if shock:
            filters.append(f"冲击维度={shock}")
        flt = "；".join(filters) if filters else "无"
        user = (
            f"日期范围：{start} ~ {end}\n过滤：{flt}\n"
            f"请列出最多 {raw_top} 个该范围内值得复盘的{market}候选事件（广撒网，含各级重要性）。"
        )
        out: _EventMineOut = self.reasoner.call(
            system=_mine_system(market), user=user, output_model=_EventMineOut,
        )
        return out.events

    @staticmethod
    def _hard_filter(
        raw: list[_MinedEventOut], start: str, end: str,
    ) -> tuple[list[_MinedEventOut], list[dict]]:
        kept: list[_MinedEventOut] = []
        dropped: list[dict] = []
        d_start, d_end = date.fromisoformat(start), date.fromisoformat(end)
        for e in raw:
            sig = _clamp(e.significance, 1, 4)
            a = _clamp(e.a_share_relevance, 1, 5)
            try:
                ann = date.fromisoformat(str(e.announcement_date)[:10])
            except ValueError:
                ann = None
            if ann is None:
                dropped.append({"description": e.description, "sig": sig, "a_share": a,
                                "原因": "日期不可解析"})
            elif not (d_start <= ann <= d_end):
                dropped.append({"description": e.description, "sig": sig, "a_share": a,
                                "原因": f"日期越界（{e.announcement_date} 不在 {start}~{end}）"})
            elif sig < 2:
                dropped.append({"description": e.description, "sig": sig, "a_share": a,
                                "原因": f"主题/噪音级（sig={sig}）"})
            elif a < 3:
                dropped.append({"description": e.description, "sig": sig, "a_share": a,
                                "原因": f"A股不可达（a_share={a}）"})
            else:
                kept.append(e)
        return kept, dropped

    def _llm_select(
        self, kept: list[_MinedEventOut], top_n: int,
    ) -> tuple[list[CandidateEvent], list[dict]]:
        if not kept:
            return [], []
        lines = []
        for i, e in enumerate(kept):
            lines.append(
                f"[{i + 1}] {e.description} | sig={_clamp(e.significance, 1, 4)} "
                f"a_share={_clamp(e.a_share_relevance, 1, 5)} "
                f"surprise={_clamp(e.surprise_timing, 1, 5)}/{_clamp(e.surprise_magnitude, 1, 5)} "
                f"impact={_clamp(e.impact_scale, 1, 5)} | "
                f"维度={e.shock_dimension} | {e.rationale}"
            )
        user = (
            f"候选事件列表（共 {len(kept)}，行号 1 起，selected 的 index 填行号）：\n" + "\n".join(lines)
            + f"\n\n请选 top {top_n} 个最值得复盘的。"
        )
        out: _EventSelectOut = self.reasoner.call(
            system=_SELECT_SYSTEM, user=user, output_model=_EventSelectOut,
        )
        selected: list[CandidateEvent] = []
        seen_idx: set[int] = set()
        for s in out.selected:
            idx = s.index - 1  # 展示行号 1-based → 列表 0-based
            if 0 <= idx < len(kept) and idx not in seen_idx:
                seen_idx.add(idx)
                cand = self._to_candidate(kept[idx])
                if cand is not None:
                    selected.append(cand)
            if len(selected) >= top_n:  # LLM 超选 → 截断
                break
        rejected: list[dict] = []
        for r in out.rejected:
            idx = r.index - 1
            if 0 <= idx < len(kept):
                e = kept[idx]
                rejected.append({
                    "description": e.description, "sig": e.significance,
                    "a_share": e.a_share_relevance, "原因": r.reason,
                })
        return selected, rejected

    @staticmethod
    def _to_candidate(e: _MinedEventOut) -> CandidateEvent | None:
        event = _build_event(e)
        if event is None:
            return None
        dedup_key = make_dedup_key(event.event_type, str(event.announcement_date), event.key_entities, description=event.description)
        return CandidateEvent(
            event=event,
            significance=_clamp(e.significance, 1, 4),
            a_share_relevance=_clamp(e.a_share_relevance, 1, 5),
            surprise_timing=_clamp(e.surprise_timing, 1, 5),
            surprise_magnitude=_clamp(e.surprise_magnitude, 1, 5),
            impact_scale=_clamp(e.impact_scale, 1, 5),
            source="llm_knowledge",
            dedup_key=dedup_key,
            industries=e.industries,
        )


def _write_json(data: Any, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_candidates(candidates: list[CandidateEvent], path: str | Path) -> Path:
    """兼容旧接口：写候选列表 json（batch-replay 用，读 final 字段）。"""
    return _write_json([_candidate_to_dict(c) for c in candidates], path)
