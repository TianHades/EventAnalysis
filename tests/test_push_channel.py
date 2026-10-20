"""PushChannel 测试：FileChannel 写文件 + WebhookChannel mock requests。"""

from datetime import date
from unittest.mock import patch

from eventreplay.pipeline.push_channel import FileChannel, WebhookChannel
from eventreplay.schema import CandidateEvent, Event


def _cand(dedup_key: str = "dk1hashhash") -> CandidateEvent:
    e = Event(
        id="e", description="测试", announcement_date=date(2024, 1, 1),
        reaction_date=date(2024, 1, 1), event_type="政策",
    )
    return CandidateEvent(event=e, significance=3, a_share_relevance=4, dedup_key=dedup_key)


def test_file_channel_writes(tmp_path):
    ch = FileChannel(tmp_path / "mon")
    ok = ch.push(title="标题", markdown="# 前瞻报告", event=_cand())
    assert ok
    f = tmp_path / "mon" / "dk1hashhash.md"
    assert f.exists()
    assert "前瞻报告" in f.read_text(encoding="utf-8")


def test_webhook_success(monkeypatch):
    import requests as _req

    class _Resp:
        status_code = 200

    monkeypatch.setattr(_req, "post", lambda *a, **k: _Resp())
    ch = WebhookChannel("https://example.com/webhook")
    assert ch.push(title="t", markdown="md", event=_cand())


def test_webhook_failure_returns_false(monkeypatch):
    import requests as _req

    def _raise(*a, **k):
        raise RuntimeError("网络")

    monkeypatch.setattr(_req, "post", _raise)
    ch = WebhookChannel("https://example.com/webhook")
    assert not ch.push(title="t", markdown="md", event=_cand())


def test_webhook_200_with_errcode_returns_false(monkeypatch, capsys):
    """企微/钉钉出错时返回 HTTP 200 + body errcode≠0 → 必须判失败并打印 errmsg。"""
    import requests as _req

    class _Resp:
        status_code = 200

        def json(self):
            return {"errcode": 40001, "errmsg": "invalid credential"}

    monkeypatch.setattr(_req, "post", lambda *a, **k: _Resp())
    ch = WebhookChannel("https://qyapi.weixin.qq.com/cgi-bin/webhook/send")
    assert not ch.push(title="t", markdown="md", event=_cand())
    out = capsys.readouterr().out
    assert "40001" in out and "invalid credential" in out


def test_webhook_200_with_errcode_zero_ok(monkeypatch):
    """企微/钉钉 200 + errcode=0 → 成功。"""
    import requests as _req

    class _Resp:
        status_code = 200

        def json(self):
            return {"errcode": 0, "errmsg": "ok"}

    monkeypatch.setattr(_req, "post", lambda *a, **k: _Resp())
    ch = WebhookChannel("https://example.com/webhook")
    assert ch.push(title="t", markdown="md", event=_cand())


def test_channel_archive_flags():
    """FileChannel 是归档渠道（is_archive=True）；实时渠道为 False。"""
    from eventreplay.pipeline.push_channel import FeishuChannel

    assert FileChannel.is_archive is True
    assert WebhookChannel.is_archive is False
    assert FeishuChannel.is_archive is False


def test_feishu_channel_push():
    """飞书 webhook 推送（interactive 卡片格式）。"""
    from eventreplay.pipeline.push_channel import FeishuChannel

    ch = FeishuChannel("https://open.feishu.cn/open-apis/bot/v2/hook/test123")
    event = CandidateEvent(
        event=Event(id="e", description="测试", announcement_date=date(2024, 1, 1), reaction_date=date(2024, 1, 1), event_type="测试"),
        significance=3, a_share_relevance=4, dedup_key="dk_feishu",
    )
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 0, "msg": "success"}
        ok = ch.push(title="前瞻", markdown="# 报告\n## 1 摘要\n内容", event=event)
    assert ok is True
    call_args = mock_post.call_args
    payload = call_args.kwargs["json"]
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "前瞻"


def test_feishu_channel_failure():
    """飞书 webhook 推送失败（code != 0）。"""
    from eventreplay.pipeline.push_channel import FeishuChannel

    ch = FeishuChannel("https://open.feishu.cn/open-apis/bot/v2/hook/bad")
    event = CandidateEvent(
        event=Event(id="e", description="测试", announcement_date=date(2024, 1, 1), reaction_date=date(2024, 1, 1), event_type="测试"),
        significance=3, a_share_relevance=4, dedup_key="dk_feishu_fail",
    )
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"code": 19021, "msg": "sign match fail"}
        ok = ch.push(title="前瞻", markdown="# 报告", event=event)
    assert ok is False


# ---- 合规披露（2026-10-19 冒烟补缺）：研究用途，非投资建议 ----


def test_feishu_card_note_contains_disclaimer():
    """飞书卡片底部 note 必须包含"研究用途，非投资建议"（同时保留前瞻免责说明）。"""
    from eventreplay.pipeline.push_channel import _md_to_feishu_card

    card = _md_to_feishu_card("前瞻", "# 报告\n## 1 摘要\n内容")
    notes = [e for e in card["card"]["elements"] if e.get("tag") == "note"]
    assert len(notes) == 1
    note_text = notes[0]["elements"][0]["content"]
    assert "研究用途，非投资建议" in note_text
    assert "前瞻预判 ≠ 确定结论" in note_text


def test_file_channel_writes_full_markdown_with_disclaimer(tmp_path):
    """FileChannel 落盘内容 = 传入完整 markdown（含前瞻报告免责声明），不截断不改写。"""
    from eventreplay.measurement.report_template import DISCLAIMER

    md = f"# 前瞻预判报告\n\n## 6 局限\n- 前瞻预判 ≠ 确定结论\n\n{DISCLAIMER}\n"
    ch = FileChannel(tmp_path / "mon")
    assert ch.push(title="前瞻", markdown=md, event=_cand("dk-disc"))
    f = tmp_path / "mon" / "dk-disc.md"
    assert f.read_text(encoding="utf-8") == md
    assert "非投资建议" in f.read_text(encoding="utf-8")


def test_webhook_passes_markdown_with_disclaimer(monkeypatch):
    """WebhookChannel（企微/钉钉）原样转发完整 markdown（含前瞻报告免责声明）。"""
    import requests as _req

    from eventreplay.measurement.report_template import DISCLAIMER

    captured: dict = {}

    class _Resp:
        status_code = 200

    def _post(url, json=None, timeout=None):
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(_req, "post", _post)
    md = f"# 前瞻预判报告\n\n## 6 局限\n- 前瞻预判 ≠ 确定结论\n\n{DISCLAIMER}\n"
    ch = WebhookChannel("https://example.com/webhook")
    assert ch.push(title="前瞻", markdown=md, event=_cand())
    assert captured["payload"]["markdown"]["text"] == md
    assert "非投资建议" in captured["payload"]["markdown"]["text"]
