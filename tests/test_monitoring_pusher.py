"""MonitoringPusher 测试：阈值过滤 + 去重 + 非事件跳过 + forward 失败重试。"""

from datetime import date

from eventreplay.pipeline.event_dedup import EventDedup
from eventreplay.pipeline.monitoring_pusher import MonitoringPusher
from eventreplay.schema import CandidateEvent, Event, NewsItem
from eventreplay.storage import Storage


def _news(i: int) -> NewsItem:
    return NewsItem(id=f"n{i}", title=f"新闻{i}", content="...", source="test")


def _cand(dedup_key: str, sig: int = 3, a_share: int = 4, surprise_timing: int = 3, surprise_magnitude: int = 3, impact_scale: int = 3) -> CandidateEvent:
    e = Event(
        id=f"e-{dedup_key}", description=f"事件{dedup_key}",
        announcement_date=date(2024, 1, 1), reaction_date=date(2024, 1, 1),
        event_type="政策",
    )
    return CandidateEvent(
        event=e, significance=sig, a_share_relevance=a_share,
        dedup_key=dedup_key, source="news",
        surprise_timing=surprise_timing, surprise_magnitude=surprise_magnitude,
        impact_scale=impact_scale,
    )


class FakeNews:
    def __init__(self, items):
        self.items = items

    def fetch_latest(self):
        return self.items


class FakeExtractor:
    def __init__(self, cands_by_news):
        self.cands = cands_by_news  # news_id -> CandidateEvent | None

    def run(self, inputs):
        return self.cands.get(inputs["news"].id)


class FakeForward:
    def __init__(self, fail_keys=None):
        self.fail_keys = fail_keys or set()
        self.calls = []

    def forward(self, *, description, date_str, mode, event_id):
        self.calls.append(event_id)
        if event_id in self.fail_keys:
            raise RuntimeError("forward 失败")
        return (f"# {description} 前瞻报告", "path")


class FakeChannel:
    def __init__(self):
        self.pushed = []

    def push(self, *, title, markdown, event):
        self.pushed.append(event.dedup_key)
        return True


def test_pushes_high_significance(tmp_path):
    """sig/a_share 达标才推送。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1), _news(2)])
    extractor = FakeExtractor({
        "n1": _cand("k1", sig=4, a_share=5),
        "n2": _cand("k2", sig=2, a_share=4),  # sig<3 过滤
    })
    ch = FakeChannel()
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [ch])
    pushed = pusher.run_once(echo=lambda x: None)
    assert pushed == 1
    assert ch.pushed == ["k1"]


def test_skips_non_event(tmp_path):
    """is_event=False（extractor 返回 None）跳过。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({"n1": None})
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FakeChannel()])
    assert pusher.run_once(echo=lambda x: None) == 0


def test_skips_already_seen(tmp_path):
    """已 seen（已推送/已 replay）不重复推送。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    dedup.mark_seen([_cand("k1")], purpose="push")  # 预标记 k1 已推送
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({"n1": _cand("k1", sig=4, a_share=5)})
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FakeChannel()])
    assert pusher.run_once(echo=lambda x: None) == 0


def test_forward_failure_continues_and_not_marked(tmp_path):
    """forward 失败 → 跳过且不标记 seen（下次重试）；其他继续。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1), _news(2)])
    extractor = FakeExtractor({
        "n1": _cand("k1", sig=4, a_share=5),
        "n2": _cand("k2", sig=4, a_share=5),
    })
    forward = FakeForward(fail_keys={"fwd-k1"})
    ch = FakeChannel()
    pusher = MonitoringPusher(news, extractor, forward, dedup, [ch])
    pushed = pusher.run_once(echo=lambda x: None)
    assert pushed == 1  # 只 k2
    assert "k1" not in ch.pushed
    assert not dedup.is_seen("k1")  # k1 失败不标记，下次重试


def test_low_a_share_filtered(tmp_path):
    """a_share_relevance 不达标过滤（重大但无 A 股标的）。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({"n1": _cand("k1", sig=4, a_share=2)})  # a_share<4
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FakeChannel()])
    assert pusher.run_once(echo=lambda x: None) == 0


def test_extractor_failure_continues(tmp_path):
    """extractor.run 抛异常 → 跳过该新闻，继续其他（不崩溃）。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1), _news(2)])

    class FailFirstExtractor:
        def __init__(self):
            self.calls = 0

        def run(self, inputs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("LLM 失败")
            return _cand("k2", sig=4, a_share=5)

    ch = FakeChannel()
    pusher = MonitoringPusher(news, FailFirstExtractor(), FakeForward(), dedup, [ch])
    pushed = pusher.run_once(echo=lambda x: None)
    assert pushed == 1  # 第一个失败跳过，第二个成功
    assert ch.pushed == ["k2"]


def test_low_surprise_filtered(tmp_path):
    """surprise_min 过滤：低突发性事件不推送。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({
        "n1": _cand("k1", sig=4, a_share=5, surprise_timing=1, surprise_magnitude=1),
    })
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FakeChannel()])
    assert pusher.run_once(surprise_min=3, echo=lambda x: None) == 0  # surprise=(1+1)/2=1 < 3


def test_low_impact_filtered(tmp_path):
    """impact_min 过滤：低影响规模事件不推送。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({
        "n1": _cand("k1", sig=4, a_share=5, impact_scale=2),
    })
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FakeChannel()])
    assert pusher.run_once(impact_min=3, echo=lambda x: None) == 0  # impact_scale=2 < 3
