"""ReportNode 单元测试：text 输出模式（成功/兜底/prompt_hash 失效缓存）。

复用 test_forward_mapping.py 的 mock 范本。ReportNode 应调 reasoner.call_text（非 call），
直出 markdown；失败兜底 render_report。
"""

from datetime import date

from eventreplay.llm import LLMNodeFailed
from eventreplay.pipeline.nodes.report import ReportNode
from eventreplay.schema import AttributionResult, Event, TransmissionChain


class _TextReasoner:
    """模拟 reasoner：call 不应被调用；call_text 按预设返回或失败。"""

    model_alias = "fake"

    def __init__(self, markdown: str = "## 1 事件摘要\n\n事件本质。\n\n===ANALYSIS===\n\n## 4 逻辑链判读分析\n\n分析内容。", fail: bool = False) -> None:
        self._md = markdown
        self._fail = fail
        self.text_calls = 0

    def call(self, **kw):  # ReportNode 不应再走 call
        raise AssertionError("ReportNode 应调 call_text 而非 call")

    def call_text(self, *, system: str, user: str, model: str | None = None) -> str:
        self.text_calls += 1
        if self._fail:
            raise LLMNodeFailed("模拟网关超时")
        return self._md


def _inputs() -> dict:
    return {
        "event": Event(
            id="e",
            description="测试事件",
            announcement_date=date(2021, 1, 1),
            reaction_date=date(2021, 1, 1),
            event_type="政策",
        ),
        "chain": TransmissionChain(event_id="e"),
        "attribution_result": AttributionResult(),
        "measurements": [],
    }


def test_report_text_success():
    """call_text 成功 → render_report 拼接 ## 1 摘要 + ## 4 判读，无兜底 banner。"""
    r = _TextReasoner(markdown="## 1 事件摘要\n\n事件本质：测试。市场主线：α。\n\n===ANALYSIS===\n\n## 4 逻辑链判读分析\n\n煤炭板块强α确认。")
    node = ReportNode(r)
    md = node.run(_inputs())
    assert "模板生成" not in md  # 无兜底 banner
    assert "事件本质：测试" in md  # ## 1 LLM 摘要
    assert "煤炭板块强α" in md  # ## 4 LLM 判读
    assert "## 2 产业链传导" in md  # 模板部分
    assert r.text_calls == 1


def test_report_text_fallback():
    """call_text 失败 → render_report 兜底（含 banner + 事件描述）。"""
    r = _TextReasoner(fail=True)
    node = ReportNode(r)
    md = node.run(_inputs())
    assert "模板生成" in md  # 兜底
    assert "测试事件" in md
    assert r.text_calls == 1


def test_report_prompt_hash_changes_on_prompt_edit():
    """改 system_prompt → prompt_hash 变（缓存失效）。"""
    node = ReportNode(_TextReasoner())
    h1 = node.prompt_hash()
    orig = node.system_prompt
    node.system_prompt = lambda: orig() + "\n新增要求：聚焦预期差。"  # type: ignore[assignment]
    try:
        h2 = node.prompt_hash()
    finally:
        node.system_prompt = orig  # type: ignore[assignment]
    assert h1 != h2, "system_prompt 变化应使 prompt_hash 改变（触发缓存重算）"
