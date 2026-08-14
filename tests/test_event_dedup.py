"""EventDedup 测试：make_dedup_key + 去重 + 跨运行持久。"""

from datetime import date

from eventreplay.pipeline.event_dedup import EventDedup, make_dedup_key
from eventreplay.schema import CandidateEvent, Event
from eventreplay.storage import Storage


def _cand(dedup_key: str = "k1", significance: int = 3) -> CandidateEvent:
    e = Event(
        id="e", description="测试事件",
        announcement_date=date(2024, 1, 1), reaction_date=date(2024, 1, 1),
        event_type="政策",
    )
    return CandidateEvent(
        event=e, significance=significance, a_share_relevance=4,
        source="news", dedup_key=dedup_key,
    )


def test_make_dedup_key_stable_and_order_independent():
    """实体顺序不同 → 同 key（排序保证）。"""
    k1 = make_dedup_key("政策", "2024-01-01", ["芯片", "美国"])
    k2 = make_dedup_key("政策", "2024-01-01", ["美国", "芯片"])
    assert k1 == k2
    k3 = make_dedup_key("政策", "2024-01-02", ["芯片", "美国"])  # 日期不同
    assert k1 != k3
    k4 = make_dedup_key("地缘", "2024-01-01", ["芯片", "美国"])  # 类型不同
    assert k1 != k4


def test_filter_new_then_mark_seen(tmp_path):
    """未见 → 全返回；mark_seen 后 → 全过滤。"""
    s = Storage(tmp_path / "t.db")
    dedup = EventDedup(s)
    cands = [_cand("k1"), _cand("k2"), _cand("k1")]  # 含列表内重复
    new = dedup.filter_new(cands)
    assert len(new) == 3  # 都未见（filter_new 只过滤已见，列表内重复也返回）
    dedup.mark_seen(new)
    assert dedup.filter_new(cands) == []


def test_persistence_across_instances(tmp_path):
    """跨 EventDedup 实例（同 db）持久化。"""
    db = tmp_path / "t.db"
    s1 = Storage(db)
    EventDedup(s1).mark_seen([_cand("k1")])
    s1.close()
    s2 = Storage(db)
    dedup2 = EventDedup(s2)
    assert dedup2.is_seen("k1")
    assert not dedup2.is_seen("k2")


def test_filter_new_skips_empty_key(tmp_path):
    """空 dedup_key 视为无效，不返回。"""
    s = Storage(tmp_path / "t.db")
    dedup = EventDedup(s)
    cands = [_cand(""), _cand("k1")]
    new = dedup.filter_new(cands)
    assert len(new) == 1
    assert new[0].dedup_key == "k1"
