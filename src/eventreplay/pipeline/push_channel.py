"""PushChannel：推送渠道抽象（webhook + 文件 + 飞书卡片，Phase 4 功能1）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from ..schema import CandidateEvent


class PushChannel(Protocol):
    """推送渠道接口。"""

    def push(self, *, title: str, markdown: str, event: CandidateEvent) -> bool: ...


class WebhookChannel:
    """企微/钉钉机器人 webhook（markdown 消息）。

    注意：企微/钉钉出错时仍返回 HTTP 200 + body errcode≠0，必须解析 body 判定。
    """

    is_archive = False  # 实时渠道（推送成功判定依据）

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.url = webhook_url
        self.timeout = timeout

    def push(self, *, title: str, markdown: str, event: CandidateEvent) -> bool:
        import requests

        payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown}}
        try:
            r = requests.post(self.url, json=payload, timeout=self.timeout)
            if r.status_code != 200:
                return False
            try:
                data = r.json()
            except Exception:
                return True  # 响应体非 JSON（部分网关无 body）：以 HTTP 状态为准
            code = data.get("errcode", data.get("code", 0))
            if code != 0:
                print(f"[push] webhook 拒绝: errcode={code} errmsg={data.get('errmsg', data.get('msg', ''))}")
                return False
            return True
        except Exception:
            return False


def _md_to_feishu_card(title: str, markdown: str) -> dict:
    """将前瞻 markdown 报告转为飞书 interactive 卡片（分章节 div + 分割线 + 底部备注）。

    飞书 lark_md 支持 **粗体**、<font color>彩色</font>、换行，但不支持表格/# 标题。
    策略：按 ## 章节拆分 → 每章一个 div（标题加粗）→ 表格转为简洁列表。
    """
    # 去掉一级标题（# 开头第一行），用 card header 替代
    lines = markdown.strip().split("\n")
    body_lines = []
    for line in lines:
        if line.startswith("# ") and not line.startswith("## "):
            continue  # 跳过一级标题
        body_lines.append(line)
    body = "\n".join(body_lines)

    # 按 ## 章节拆分
    sections: list[tuple[str, str]] = []
    current_header = ""
    current_lines: list[str] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            if current_header or current_lines:
                sections.append((current_header, "\n".join(current_lines).strip()))
            current_header = line.replace("## ", "").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_header or current_lines:
        sections.append((current_header, "\n".join(current_lines).strip()))

    # 构建卡片元素
    elements: list[dict] = []
    for i, (header, content) in enumerate(sections):
        if not content:
            continue
        # 转换 markdown 表格为简洁列表
        content = _simplify_tables(content)
        # 章节标题加粗 + 内容
        if header:
            md_text = f"**{header}**\n{content}"
        else:
            md_text = content
        # 截断单元素长度（飞书单 div 上限 ~3000）
        if len(md_text) > 2900:
            md_text = md_text[:2900] + "\n...(截断)"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": md_text}})
        # 章节间加分割线（最后一个不加）
        if i < len(sections) - 1:
            elements.append({"tag": "hr"})

    # 底部备注（前瞻免责声明）
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "⚠️ 前瞻预判 ≠ 确定结论，历史不一定重演 | EventAnalysis"}],
    })

    # 卡片配色（按方向选色）
    header_template = "blue"  # 默认蓝
    if "利空" in title or "暴跌" in title or "崩" in title:
        header_template = "red"
    elif "利好" in title or "暴涨" in title:
        header_template = "green"

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "title": {"tag": "plain_text", "content": title[:50]},
                "template": header_template,
            },
            "elements": elements,
        },
    }


def _simplify_tables(text: str) -> str:
    """将 markdown 表格转为简洁列表（飞书不支持表格渲染）。

    | 代码 | 名称 | 方向 | → **代码 名称** 方向
    """
    lines = text.split("\n")
    out: list[str] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # 跳过分隔行 |---|---|
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            # 跳过表头（第一个非分隔行）的列名行标记，但仍保留
            if not in_table:
                # 表头行：加粗
                out.append("｜ ".join(f"**{c}**" for c in cells if c))
                in_table = True
            else:
                out.append("｜ ".join(c for c in cells if c))
        else:
            if in_table:
                in_table = False
            out.append(line)
    return "\n".join(out)


class FeishuChannel:
    """飞书自定义机器人 webhook（interactive 卡片格式）。

    URL 格式：https://open.feishu.cn/open-apis/bot/v2/hook/{hook_id}
    使用 interactive 卡片：分章节 div + 分割线 + 底部备注，比纯文本美观。
    """

    is_archive = False  # 实时渠道（推送成功判定依据）

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.url = webhook_url
        self.timeout = timeout

    def push(self, *, title: str, markdown: str, event: CandidateEvent) -> bool:
        import requests

        payload = _md_to_feishu_card(title, markdown)
        try:
            r = requests.post(self.url, json=payload, timeout=self.timeout)
            data = r.json()
            code = data.get("code", data.get("StatusCode", -1))
            return r.status_code == 200 and code == 0
        except Exception:
            return False


class FileChannel:
    """写本地文件（reports/monitoring/）。

    归档渠道：push 恒成功，不作为"推送是否成功"的判定依据
    （否则实时渠道全挂时仍会 mark_seen，架空重试设计）。仅当配置里
    只有 archive 渠道时才以其为准（事件不丢）。
    """

    is_archive = True  # 归档渠道标记（monitoring_pusher 的 pushed_ok 判定跳过）

    def __init__(self, out_dir: str | Path = "reports/monitoring") -> None:
        self.out_dir = Path(out_dir)

    def push(self, *, title: str, markdown: str, event: CandidateEvent) -> bool:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{event.dedup_key[:12] or 'event'}.md"
        (self.out_dir / fname).write_text(markdown, encoding="utf-8")
        return True
