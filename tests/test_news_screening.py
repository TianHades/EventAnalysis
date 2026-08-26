"""NewsScreeningNode 测试：批量初筛索引健壮性（1-based/越界/dup_of 归并/批次失败降级）
+ 标题免费去重 + 分批调用。"""

from eventreplay.pipeline.nodes.news_screening import (
    SCREEN_BATCH_SIZE,
    NewsScreeningNode,
    _ScreenItemOut,
    _ScreenOut,
    dedup_by_title,
)
from eventreplay.schema import NewsItem


def _item(i: int, title: str | None = None, content: str = "内容") -> NewsItem:
    return NewsItem(id=f"n{i}", title=title or f"新闻标题{i}", content=content, source="test")


class FakeReasoner:
    """按调用次序返回预设输出；记录每次 user prompt 供断言。"""

    model_alias = "fake"

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls: list[str] = []

    def call(self, *, system, user, output_model):
        assert output_model is _ScreenOut
        self.calls.append(user)
        return self._outputs.pop(0) if self._outputs else _ScreenOut(keep=[])


def _node(outputs) -> tuple[NewsScreeningNode, FakeReasoner]:
    r = FakeReasoner(outputs)
    return NewsScreeningNode(r), r


def test_screen_one_based_indices_and_order():
    """keep 索引 1-based：保留 1/3 → 幸存为第 1、3 条（保持原顺序）。"""
    node, _ = _node([_ScreenOut(keep=[_ScreenItemOut(index=3), _ScreenItemOut(index=1)])])
    items = [_item(i) for i in range(1, 4)]
    survivors = node.screen(items, echo=lambda s: None)
    assert [s.id for s in survivors] == ["n1", "n3"]


def test_screen_out_of_range_and_duplicate_indices_dropped():
    """越界索引丢弃；重复索引不产生重复幸存者。"""
    node, _ = _node([_ScreenOut(keep=[
        _ScreenItemOut(index=1), _ScreenItemOut(index=1), _ScreenItemOut(index=99),
        _ScreenItemOut(index=0),
    ])])
    survivors = node.screen([_item(1), _item(2)], echo=lambda s: None)
    assert [s.id for s in survivors] == ["n1"]


def test_screen_dup_of_merges_cross_source():
    """dup_of=主条 → 副本跳过细评（跨源同事件归并）。"""
    node, _ = _node([_ScreenOut(keep=[
        _ScreenItemOut(index=1),            # 主条
        _ScreenItemOut(index=2, dup_of=1),  # 同事件副本
        _ScreenItemOut(index=3),
    ])])
    survivors = node.screen([_item(i) for i in range(1, 4)], echo=lambda s: None)
    assert [s.id for s in survivors] == ["n1", "n3"]


def test_screen_dup_of_orphan_keeps_item():
    """dup_of 指向未保留/无效条目 → 本条独立保留（不因主条被筛掉而丢事件）。"""
    node, _ = _node([_ScreenOut(keep=[
        _ScreenItemOut(index=2, dup_of=1),  # 主条 1 未被保留
        _ScreenItemOut(index=3, dup_of=99), # 主条越界
    ])])
    survivors = node.screen([_item(i) for i in range(1, 4)], echo=lambda s: None)
    assert [s.id for s in survivors] == ["n2", "n3"]


def test_screen_batch_failure_keeps_all():
    """初筛调用失败 → 本批全保留（宽松降级，细评兜底）。"""
    class Boom(Exception):
        pass

    class FailReasoner:
        model_alias = "fake"

        def call(self, *, system, user, output_model):
            raise Boom("网关超时")

    node = NewsScreeningNode(FailReasoner())
    items = [_item(i) for i in range(1, 4)]
    assert node.screen(items, echo=lambda s: None) == items


def test_screen_chunks_into_batches():
    """超过 SCREEN_BATCH_SIZE 分批：每批一次独立调用。"""
    n = SCREEN_BATCH_SIZE + 5
    node, reasoner = _node([_ScreenOut(keep=[_ScreenItemOut(index=1)]), _ScreenOut(keep=[])])
    survivors = node.screen([_item(i) for i in range(1, n + 1)], echo=lambda s: None)
    assert len(reasoner.calls) == 2
    assert f"[{SCREEN_BATCH_SIZE}]" in reasoner.calls[0]  # 第一批含第 20 条
    assert "[1]" in reasoner.calls[1]  # 第二批重新从 1 编号
    assert [s.id for s in survivors] == ["n1"]


def test_build_user_truncates_snippet():
    """内容摘要截断到 300 字（判定"是否事件"标题+摘要素材足够）。"""
    node, reasoner = _node([_ScreenOut(keep=[])])
    node.run({"items": [_item(1, content="长" * 1000)]})
    assert "长" * 300 in reasoner.calls[0]
    assert "长" * 301 not in reasoner.calls[0]


def test_dedup_by_title():
    """归一化标题相同的跨源重复去掉；不同标题/空标题保留。"""
    items = [
        _item(1, title="央行降准 0.25 个百分点！"),
        _item(2, title="央行降准0.25个百分点"),   # 去空白/标点后同标题
        _item(3, title="另一条新闻"),
        _item(4, title=None),                     # 空标题保留（交初筛判断）
        _item(5, title="另一条新闻"),             # 与 n3 重复
    ]
    out = dedup_by_title(items)
    assert [x.id for x in out] == ["n1", "n3", "n4"]
