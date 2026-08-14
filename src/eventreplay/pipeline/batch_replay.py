"""BatchReplayRunner：批量 replay + extract-pattern（Phase 4 功能2建库）。

逐个候选事件跑 ReplayOrchestrator.replay + PatternService.extract_pattern，
断点续跑（EventDedup 标记已 replay），失败不中断，进度打印。
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable

from ..schema import CandidateEvent, Event, ImpactDirection, ShockDimension
from .event_dedup import EventDedup


def read_candidates(path: str | Path) -> list[CandidateEvent]:
    """读 mining_candidates json → CandidateEvent[]（用户审核编辑后的文件）。

    兼容新格式 {final: [...], ...} 与旧格式 [...候选...]。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "final" in data:
        data = data["final"]  # 新格式取 final
    out: list[CandidateEvent] = []
    for d in data:
        dk = d.get("dedup_key") or ""
        if not dk:
            continue  # 容错：缺 dedup_key 跳过（用户编辑漏字段）
        try:
            sd = ShockDimension(d["shock_dimension"]) if d.get("shock_dimension") else None
        except (ValueError, KeyError):
            sd = None
        try:
            idir = ImpactDirection(d["impact_direction"]) if d.get("impact_direction") else None
        except (ValueError, KeyError):
            idir = None
        ann_str = d.get("announcement_date") or date.today().isoformat()
        try:
            ann = date.fromisoformat(str(ann_str)[:10])
        except ValueError:
            ann = date.today()
        event = Event(
            id=f"evt-{dk[:12]}",
            description=d.get("description", ""),
            announcement_date=ann,
            reaction_date=ann,
            event_type=d.get("event_type", ""),
            shock_dimension=sd,
            impact_direction=idir,
            key_entities=d.get("key_entities", []),
        )
        out.append(CandidateEvent(
            event=event,
            significance=max(1, min(4, d.get("significance", 2))),
            a_share_relevance=max(1, min(5, d.get("a_share_relevance", 3))),
            surprise_timing=max(1, min(5, d.get("surprise_timing", 3))),
            surprise_magnitude=max(1, min(5, d.get("surprise_magnitude", 3))),
            impact_scale=max(1, min(5, d.get("impact_scale", 3))),
            source=d.get("source", "llm_knowledge"),
            dedup_key=dk,
            industries=d.get("industries", []),
        ))
    return out


class BatchReplayRunner:
    """批量 replay + extract-pattern + 断点续跑（EventDedup）+ 失败不中断。"""

    def __init__(self, replay_orchestrator, pattern_service, dedup: EventDedup, benchmark: str = "sh000300") -> None:
        self.replay_orch = replay_orchestrator
        self.ps = pattern_service
        self.dedup = dedup
        self.benchmark = benchmark

    def _replay_id(self, c: CandidateEvent) -> str:
        return f"replay-{c.dedup_key[:12]}"

    def _already_replayed(self, c: CandidateEvent) -> bool:
        return self.dedup.is_seen(c.dedup_key, purpose="replay")

    def run(
        self,
        candidates: list[CandidateEvent],
        sleep_s: float = 0.0,
        echo: Callable[[str], None] = print,
    ) -> dict[str, int]:
        """批量 replay + extract；返回 {total, replayed, failed, skipped}。"""
        total = len(candidates)
        replayed = failed = skipped = 0
        for i, c in enumerate(candidates):
            if self._already_replayed(c):
                skipped += 1
                echo(f"[{i+1}/{total}] skip(已replay) {c.event.description[:30]}")
                continue
            rid = self._replay_id(c)
            try:
                e = c.event
                self.replay_orch.replay(
                    description=e.description,
                    announcement_date=str(e.announcement_date),
                    reaction_date=str(e.reaction_date),
                    event_id=rid,
                    benchmark=self.benchmark,
                )
                self.ps.extract_pattern(rid)
                self.dedup.mark_seen([c], purpose="replay")  # 标记已 replay（断点续跑）
                replayed += 1
                echo(f"[{i+1}/{total}] ok {c.event.description[:30]}")
            except Exception as ex:
                failed += 1
                echo(f"[{i+1}/{total}] FAIL {c.event.description[:30]}: {str(ex)[:80]}")
            if sleep_s:
                time.sleep(sleep_s)
        return {"total": total, "replayed": replayed, "failed": failed, "skipped": skipped}
