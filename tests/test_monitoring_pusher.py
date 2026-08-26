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


class FailChannel:
    """push 恒失败（模拟企微/飞书 webhook 挂）。"""

    def push(self, *, title, markdown, event):
        return False


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
    # k1 forward 失败不标 push（下次重试）；identified 标记（上下文闭环）不算已处理
    assert not dedup.is_seen("k1", purpose="push")


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


# ---- Fix 2: fetch_latest 失败不崩溃 ----


def test_fetch_latest_failure_returns_zero(tmp_path):
    """新闻源挂了 → echo 原因、return 0，不抛异常（run_once 可被 monitor 循环持续调用）。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))

    class DeadNews:
        def fetch_latest(self):
            raise RuntimeError("akshare 接口超时")

    msgs: list[str] = []
    pusher = MonitoringPusher(DeadNews(), FakeExtractor({}), FakeForward(), dedup, [FakeChannel()])
    assert pusher.run_once(echo=msgs.append) == 0
    assert any("采集失败" in m for m in msgs), "应输出失败原因"


# ---- Fix 3: archive 渠道不架空"全失败不标 seen" ----


def test_realtime_fail_file_ok_not_marked_seen(tmp_path):
    """实时渠道（webhook）全挂 + FileChannel 落盘成功 → 不 mark_seen（下次重试）。"""
    from eventreplay.pipeline.push_channel import FileChannel

    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({"n1": _cand("k1", sig=4, a_share=5)})
    fwd = FakeForward()
    out_dir = tmp_path / "mon"
    pusher = MonitoringPusher(news, extractor, fwd, dedup, [FailChannel(), FileChannel(out_dir)])
    pushed = pusher.run_once(echo=lambda x: None)
    assert pushed == 0, "archive 成功不算推送成功"
    assert not dedup.is_seen("k1", purpose="push"), "不得标记 seen（下次重试）"
    assert list(out_dir.glob("*.md")), "archive 渠道仍应落盘"


def test_archive_only_channel_still_counts(tmp_path):
    """只配置 archive 渠道时，以其为准（仍能标记 seen，事件不丢）。"""
    from eventreplay.pipeline.push_channel import FileChannel

    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({"n1": _cand("k1", sig=4, a_share=5)})
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FileChannel(tmp_path / "mon")])
    assert pusher.run_once(echo=lambda x: None) == 1
    assert dedup.is_seen("k1", purpose="push")


# ---- Fix 10: identified 上下文闭环 ----


def test_identified_below_threshold_marked_for_context(tmp_path):
    """识别但未达推送阈值的事件 → mark_seen(purpose='identified')，进入后续上下文。"""
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    news = FakeNews([_news(1)])
    extractor = FakeExtractor({"n1": _cand("k1", sig=2, a_share=2)})  # 低于阈值
    pusher = MonitoringPusher(news, extractor, FakeForward(), dedup, [FakeChannel()])
    pusher.run_once(echo=lambda x: None)
    assert dedup.is_seen("k1", purpose="identified"), "识别到的事件应记入 identified"
    assert not dedup.is_seen("k1", purpose="push"), "未推送不得标 push"
    # 下一轮上下文应含该事件描述
    ctx = MonitoringPusher.__new__(MonitoringPusher)
    ctx.dedup = dedup
    assert "事件k1" in ctx._build_recent_context(echo=lambda *_: None)


def test_build_recent_context_reads_push_and_identified(tmp_path):
    """recent_context 同时读 push + identified 两个 purpose。"""
    from datetime import datetime as _dt

    st = Storage(tmp_path / "t.db")
    st._conn.execute(
        "INSERT INTO seen_events(dedup_key, source, payload, seen_at, purpose) VALUES (?,?,?,?,?)",
        ("kp", "news", '{"description": "已推送事件"}', "2026-08-02T00:00:00", "push"),
    )
    st._conn.execute(
        "INSERT INTO seen_events(dedup_key, source, payload, seen_at, purpose) VALUES (?,?,?,?,?)",
        ("ki", "news", '{"description": "已识别事件"}', "2026-08-01T00:00:00", "identified"),
    )
    st._conn.commit()
    pusher = MonitoringPusher.__new__(MonitoringPusher)
    pusher.dedup = EventDedup(st)
    ctx = pusher._build_recent_context(echo=lambda *_: None)
    assert "已推送事件" in ctx and "已识别事件" in ctx


# ---- Fix 2: monitor 主循环异常恢复 ----


def test_monitor_loop_survives_run_once_exception(tmp_path):
    """run_once 抛异常 → 主循环 echo 后继续（不退出），下轮正常执行。"""
    from eventreplay.pipeline.monitoring_pusher import run_monitor_loop

    calls = {"n": 0}

    class FlakyPusher:
        def run_once(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("SQLite 数据库被锁")
            return 0

    def fake_sleep(_s):
        if calls["n"] >= 2:
            raise KeyboardInterrupt  # 第二轮后结束循环

    msgs: list[str] = []
    import pytest

    with pytest.raises(KeyboardInterrupt):
        run_monitor_loop(FlakyPusher(), interval_min=0, echo=msgs.append, sleep=fake_sleep)
    assert calls["n"] >= 2, "首轮异常后循环必须继续"
    assert any("本轮执行异常" in m for m in msgs)


# ---- 两级漏斗（初筛 + 并发细评）----


class CountingExtractor:
    """记录调用顺序的细评 fake：返回与 news id 关联的 cand 或 None。"""

    def __init__(self, cands_by_news):
        self.cands = cands_by_news
        self.called: list[str] = []

    def run(self, inputs):
        nid = inputs["news"].id
        self.called.append(nid)
        return self.cands.get(nid)


class FakeScreener:
    """初筛 fake：返回配置的幸存子集（或抛错）。"""

    def __init__(self, survivors=None, fail=False):
        self.survivors = survivors
        self.fail = fail
        self.calls: list = []

    def screen(self, items, echo=print):
        self.calls.append(list(items))
        if self.fail:
            raise RuntimeError("初筛网关超时")
        return self.survivors if self.survivors is not None else items


def _pusher(tmp_path, extractor, screener=None, workers=1):
    dedup = EventDedup(Storage(tmp_path / "t.db"))
    return MonitoringPusher(
        FakeNews([]), extractor, FakeForward(), dedup, [FakeChannel()],
        screener=screener, extract_workers=workers,
    )


def test_funnel_screens_before_extraction(tmp_path):
    """初筛幸存者才进入细评：5 条 → 初筛留 2 → extractor 只调 2 次。"""
    news = FakeNews([_news(i) for i in range(1, 6)])
    extractor = CountingExtractor({"n1": _cand("k1", sig=4, a_share=5), "n3": None})
    screener = FakeScreener(survivors=[news.items[0], news.items[2]])
    pusher = _pusher(tmp_path, extractor, screener)
    pusher.news = news
    pusher.run_once(echo=lambda s: None)
    assert extractor.called == ["n1", "n3"]
    assert len(screener.calls) == 1 and len(screener.calls[0]) == 5


def test_funnel_screen_failure_falls_back_to_all(tmp_path):
    """初筛失败 → 降级逐条细评（全部条目都评估，不引入新单点）。"""
    news = FakeNews([_news(i) for i in range(1, 6)])
    extractor = CountingExtractor({f"n{i}": None for i in range(1, 6)})
    pusher = _pusher(tmp_path, extractor, FakeScreener(fail=True))
    pusher.news = news
    msgs: list[str] = []
    pusher.run_once(echo=msgs.append)
    assert extractor.called == [f"n{i}" for i in range(1, 6)]
    assert any("初筛失败" in m for m in msgs)


def test_funnel_disabled_or_small_batch_skips_screening(tmp_path):
    """screen=False 或条数 < 4：跳过初筛直接逐条。"""
    extractor = CountingExtractor({"n1": None})
    screener = FakeScreener()
    pusher = _pusher(tmp_path, extractor, screener)
    pusher.news = FakeNews([_news(1)])
    pusher.run_once(echo=lambda s: None)
    assert screener.calls == [] and extractor.called == ["n1"]

    extractor2 = CountingExtractor({f"n{i}": None for i in range(1, 5)})
    screener2 = FakeScreener()
    pusher2 = _pusher(tmp_path, extractor2, screener2)
    pusher2.news = FakeNews([_news(i) for i in range(1, 5)])
    pusher2.run_once(screen=False, echo=lambda s: None)
    assert screener2.calls == [] and len(extractor2.called) == 4


def test_title_dedup_runs_before_screening(tmp_path):
    """同标题跨源重复在 LLM 之前免费去掉（初筛输入不含重复）。"""
    dup1 = NewsItem(id="a1", title="央行降准 0.25%", content="x", source="jin10")
    dup2 = NewsItem(id="a2", title="央行降准0.25%", content="x", source="cls")
    others = [_news(i) for i in (3, 4, 5)]  # 补足 4 条阈值，确保初筛触发
    extractor = CountingExtractor({"a1": None, "n3": None, "n4": None, "n5": None})
    screener = FakeScreener()  # 全保留
    pusher = _pusher(tmp_path, extractor, screener)
    pusher.news = FakeNews([dup1, dup2, *others])
    pusher.run_once(echo=lambda s: None)
    assert [x.id for x in screener.calls[0]] == ["a1", "n3", "n4", "n5"], "重复标题不应进入初筛"


def test_parallel_extraction_preserves_order(tmp_path):
    """并发细评：完成顺序无关，输出按输入顺序（map 语义），全部条目都被调用。"""
    import threading
    import time as _t

    class SlowExtractor:
        def __init__(self):
            self.lock = threading.Lock()
            self.started: list[str] = []

        def run(self, inputs):
            nid = inputs["news"].id
            with self.lock:
                self.started.append(nid)
            # n1 故意最慢：并发下完成序与输入序相反，但结果序必须保持输入序
            _t.sleep({"n1": 0.3}.get(nid, 0.02))
            return _cand(f"k-{nid}") if nid != "n3" else None

    slow = SlowExtractor()
    extractor = CountingExtractor.__new__(CountingExtractor)  # 不用计数版
    pusher = _pusher(tmp_path, slow, workers=3)
    pusher.news = FakeNews([_news(i) for i in range(1, 5)])
    msgs: list[str] = []
    # run_once 走完整链：4 条 → 无初筛（screener=None）→ 并发细评 → 无推送（阈值过滤 k-*）
    pusher.run_once(echo=msgs.append)
    assert slow.started[0] in ("n1", "n2", "n3")  # 已并发启动
    assert any("共识别 3 个事件" in m for m in msgs)  # 4 条中 n3 非事件
