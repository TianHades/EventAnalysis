"""BatchReplayRunner 测试：批量 replay + 断点续跑 + 失败不中断 + read_candidates。"""

import json
from datetime import date

from eventreplay.pipeline.batch_replay import BatchReplayRunner, read_candidates
from eventreplay.pipeline.event_dedup import EventDedup
from eventreplay.schema import CandidateEvent, Event
from eventreplay.storage import Storage


def _cand(dedup_key: str, desc: str = "测试事件") -> CandidateEvent:
    e = Event(
        id=f"evt-{dedup_key[:12]}", description=desc,
        announcement_date=date(2023, 1, 1), reaction_date=date(2023, 1, 1),
        event_type="政策",
    )
    return CandidateEvent(
        event=e, significance=3, a_share_relevance=4,
        source="llm_knowledge", dedup_key=dedup_key,
    )


class FakeReplayOrch:
    def __init__(self, fail_keys: set[str] | None = None):
        self.fail_keys = fail_keys or set()
        self.calls: list[str] = []

    def replay(self, *, description, announcement_date, reaction_date, event_id, benchmark="sh000300"):
        self.calls.append(event_id)
        if event_id in self.fail_keys:
            raise RuntimeError("模拟失败")


class FakePatternService:
    def __init__(self):
        self.extracted: list[str] = []

    def extract_pattern(self, replay_id):
        self.extracted.append(replay_id)
        return None


def test_batch_replay_runs_all(tmp_path):
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    orch = FakeReplayOrch()
    ps = FakePatternService()
    runner = BatchReplayRunner(orch, ps, dedup)
    cands = [_cand("k1xxxxxxxx"), _cand("k2xxxxxxxx"), _cand("k3xxxxxxxx")]
    prog = runner.run(cands, echo=lambda x: None)
    assert prog == {"total": 3, "replayed": 3, "failed": 0, "skipped": 0}
    assert len(orch.calls) == 3
    assert len(ps.extracted) == 3


def test_batch_replay_skips_already(tmp_path):
    """已 replay（seen）→ skip。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    dedup.mark_seen([_cand("k1xxxxxxxx")], purpose="replay")  # 预标记 k1 已 replay
    orch = FakeReplayOrch()
    runner = BatchReplayRunner(orch, FakePatternService(), dedup)
    cands = [_cand("k1xxxxxxxx"), _cand("k2xxxxxxxx")]
    prog = runner.run(cands, echo=lambda x: None)
    assert prog["replayed"] == 1
    assert prog["skipped"] == 1
    assert len(orch.calls) == 1  # 只 replay k2


def test_batch_replay_failure_continues(tmp_path):
    """中间失败 → 计 failed，继续其他。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    orch = FakeReplayOrch(fail_keys={"replay-k2xxxxxxxx"})
    runner = BatchReplayRunner(orch, FakePatternService(), dedup)
    cands = [_cand("k1xxxxxxxx"), _cand("k2xxxxxxxx"), _cand("k3xxxxxxxx")]
    prog = runner.run(cands, echo=lambda x: None)
    assert prog["replayed"] == 2
    assert prog["failed"] == 1
    assert prog["skipped"] == 0


def test_batch_replay_persistence_across_runs(tmp_path):
    """跨 run 实例：已 replay 的重跑跳过（断点续跑）。"""
    db = tmp_path / "t.db"
    cands = [_cand("k1xxxxxxxx"), _cand("k2xxxxxxxx")]
    runner1 = BatchReplayRunner(FakeReplayOrch(), FakePatternService(), EventDedup(Storage(db)))
    runner1.run(cands, echo=lambda x: None)
    # 第二次跑（新实例，同 db）
    runner2 = BatchReplayRunner(FakeReplayOrch(), FakePatternService(), EventDedup(Storage(db)))
    prog = runner2.run(cands, echo=lambda x: None)
    assert prog["replayed"] == 0
    assert prog["skipped"] == 2  # 全已 replay


def test_read_candidates(tmp_path):
    """读 mining_candidates json → CandidateEvent[]。"""
    data = [{
        "dedup_key": "dk1hashhash", "description": "芯片管制升级", "event_type": "地缘",
        "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2023-10-07", "key_entities": ["美国", "中国"],
        "industries": ["电子"], "significance": 3, "a_share_relevance": 4,
        "source": "llm_knowledge",
    }]
    p = tmp_path / "cands.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cands = read_candidates(p)
    assert len(cands) == 1
    assert cands[0].event.description == "芯片管制升级"
    assert cands[0].event.shock_dimension.value == "政策"
    assert cands[0].event.announcement_date == date(2023, 10, 7)
    assert cands[0].significance == 3
    assert cands[0].dedup_key == "dk1hashhash"
    assert cands[0].industries == ["电子"]
