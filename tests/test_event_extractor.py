"""EventExtractor 测试：LLM 抽取 + 4级打分 + 噪音过滤 + dedup_key 稳定。"""

from datetime import date, datetime

from eventreplay.pipeline.nodes.event_extractor import EventExtractor, _EventExtractOut
from eventreplay.schema import NewsItem


class FakeReasoner:
    model_alias = "fake"

    def __init__(self, output: _EventExtractOut):
        self._output = output

    def call(self, *, system, user, output_model):
        assert output_model is _EventExtractOut
        return self._output


def _news() -> NewsItem:
    return NewsItem(
        id="n1", title="美国对华芯片管制升级",
        content="限制先进制程设备与EDA出口...",
        published_at=datetime(2026, 7, 1), source="jiuyan",
    )


def _event_out() -> _EventExtractOut:
    return _EventExtractOut(
        is_event=True, event_type="地缘", shock_dimension="政策", impact_direction="利空",
        description="美国对华芯片管制升级",
        announcement_date="2026-07-01", reaction_date="2026-07-01",
        key_entities=["美国", "中国", "芯片"], industries=["电子"],
        significance=3, a_share_relevance=5, rationale="主线级+A股可达",
    )


def test_extract_returns_candidate_event():
    node = EventExtractor(FakeReasoner(_event_out()))
    out = node.run({"news": _news()})
    assert out is not None
    assert out.significance == 3
    assert out.a_share_relevance == 5
    assert out.source == "news"
    assert out.news_id == "n1"
    # Event 字段
    assert out.event.event_type == "地缘"
    assert out.event.description == "美国对华芯片管制升级"
    assert out.event.announcement_date == date(2026, 7, 1)
    assert out.event.shock_dimension.value == "政策"
    assert out.event.impact_direction.value == "利空"
    assert out.event.key_entities == ["美国", "中国", "芯片"]
    assert out.dedup_key  # 非空


def test_extract_filters_non_event():
    """is_event=False → None（噪音过滤）。"""
    noise = _event_out().model_copy(update={"is_event": False})
    node = EventExtractor(FakeReasoner(noise))
    assert node.run({"news": _news()}) is None


def test_extract_invalid_shock_dimension_defaults_none():
    """shock_dimension 非法 → None（不抛异常）。"""
    out_bad = _event_out().model_copy(update={"shock_dimension": "无效维度"})
    node = EventExtractor(FakeReasoner(out_bad))
    out = node.run({"news": _news()})
    assert out is not None
    assert out.event.shock_dimension is None


def test_extract_invalid_impact_direction_defaults_none():
    """impact_direction 非法 → None。"""
    out_bad = _event_out().model_copy(update={"impact_direction": "不对"})
    node = EventExtractor(FakeReasoner(out_bad))
    out = node.run({"news": _news()})
    assert out is not None
    assert out.event.impact_direction is None


def test_dedup_key_stable():
    """同事件同输入 → 同 dedup_key（跨调用稳定）。"""
    node = EventExtractor(FakeReasoner(_event_out()))
    out1 = node.run({"news": _news()})
    out2 = node.run({"news": _news()})
    assert out1.dedup_key == out2.dedup_key


def test_dedup_key_differs_for_different_events():
    """不同事件（不同描述）→ 不同 dedup_key（新逻辑用 description 而非 entities）。"""
    node = EventExtractor(FakeReasoner(_event_out()))
    out1 = node.run({"news": _news()})
    out2 = node.run({"news": _news()})
    assert out1.dedup_key == out2.dedup_key  # 同输入同 key
    # 不同描述 → 不同 key（同事件不同 entities 仍同 key）
    other = _event_out().model_copy(update={"description": "另一个完全不同的事件"})
    node2 = EventExtractor(FakeReasoner(other))
    out3 = node2.run({"news": _news()})
    assert out1.dedup_key != out3.dedup_key


def test_extract_invalid_date_falls_back_today():
    """LLM 返回非法日期 → 兜底 today，不崩溃（与 historical_miner 一致）。"""
    out = _event_out().model_copy(update={
        "announcement_date": "非法日期", "reaction_date": "也非法",
    })
    node = EventExtractor(FakeReasoner(out))
    cand = node.run({"news": _news()})
    assert cand is not None
    assert cand.event.announcement_date == date.today()


def test_extract_significance_clamped():
    """significance/a_share 越界 → clamp 到 1-4 / 1-5。"""
    out = _event_out().model_copy(update={"significance": 5, "a_share_relevance": 9})
    node = EventExtractor(FakeReasoner(out))
    cand = node.run({"news": _news()})
    assert cand.significance == 4  # 5→4
    assert cand.a_share_relevance == 5  # 9→5

    out2 = _event_out().model_copy(update={"significance": 0, "a_share_relevance": -1})
    node2 = EventExtractor(FakeReasoner(out2))
    cand2 = node2.run({"news": _news()})
    assert cand2.significance == 1  # 0→1
    assert cand2.a_share_relevance == 1  # -1→1
