"""新闻初筛节点（两级漏斗第一级）：批量 LLM 粗筛，砍掉明显非事件。

背景：monitor 每轮对每条新闻做一次完整抽取（multi 48 条 ≈ 33 分钟），
其中 ~80% 是行情播报/例行数据等非事件。初筛一次调用处理一批
（标题+内容前 300 字），幸存者才进入逐条细评（EventExtractor）。

设计要点：
- 宽松优先（宁可多留不可错杀）：只剔除明显非事件，严格打分留给细评；
  单批调用失败时该批全保留（筛不动就全留，不引入新单点）
- 索引健壮性照搬 historical_miner._llm_select 的修复经验：
  1-based 展示、去重、越界丢弃、dup_of 指向无效/未保留条目时视为独立保留
- 每批 ≤ SCREEN_BATCH_SIZE 条，批间独立调用
"""

from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...schema.news import NewsItem
from .base import LLMNode

SCREEN_BATCH_SIZE = 20
_SNIPPET_LEN = 300


class _ScreenItemOut(BaseModel):
    index: int  # 1-based
    dup_of: int | None = None  # 与另一条同事件（保留那条主条，本条跳过细评）


class _ScreenOut(BaseModel):
    keep: list[_ScreenItemOut] = Field(default_factory=list)


def dedup_by_title(items: list[NewsItem]) -> list[NewsItem]:
    """跨源同标题免费去重（LLM 之前的纯 Python 预处理，归一化后精确匹配）。

    只去标题完全相同的跨源重复（multi 源常见同稿转发）；标题为空的条目保留
    （交由初筛判断）。
    """
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in items:
        norm = re.sub(r"[\s　\W]+", "", (it.title or "").lower())
        if norm:
            if norm in seen:
                continue
            seen.add(norm)
        out.append(it)
    return out


class NewsScreeningNode(LLMNode):
    """批量初筛：N 条新闻 → 幸存子集（含跨源同事件归并）。"""

    node_id = "news_screening"
    output_model = _ScreenOut

    def system_prompt(self) -> str:
        return (
            "你是 A 股新闻初筛员。输入是一批编号新闻（格式：[i] 标题 | 内容摘要），"
            "任务：判断每条是否值得进入下一步的细致事件评估。\n"
            "剔除（仅限明显不是事件的）：行情/收盘播报、无意外的例行数据发布"
            "（CPI/PMI/持仓等日程数据）、个股盘中异动播报、普通公告摘要转载、"
            "广告软文、生活/体育/娱乐资讯、与市场无关的国际新闻。\n"
            "保留（宁可多留不可错杀，不确定=保留）：政策/监管/地缘/军事冲突/"
            "宏观突发（加息、汇率异动、贸易制裁）/重大公司动作（巨额投资、制裁、"
            "IPO 变故）/技术突破/大宗商品剧烈波动等可能影响 A 股或全球主线的事件。\n"
            "同一事件被多家报道：保留信息最全的一条为主条，其余条目输出 dup_of=主条编号。\n"
            "只输出 JSON：{\"keep\": [{\"index\": 编号, \"dup_of\": 主条编号或 null}]}，"
            "不保留的条目不要出现在 keep 里。"
        )

    def build_user(self, inputs: dict[str, Any]) -> str:
        lines = []
        for i, item in enumerate(inputs["items"], 1):
            snippet = (item.content or "")[:_SNIPPET_LEN].replace("\n", " ")
            lines.append(f"[{i}] {item.title} | {snippet}")
        return "\n".join(lines)

    def screen(self, items: list[NewsItem], echo: Callable[[str], None] = print) -> list[NewsItem]:
        """分批粗筛，返回幸存条目（保持原顺序）。单批失败该批全保留（宽松降级）。"""
        survivors: list[NewsItem] = []
        for lo in range(0, len(items), SCREEN_BATCH_SIZE):
            batch = items[lo : lo + SCREEN_BATCH_SIZE]
            survivors.extend(self._screen_batch(batch, echo=echo))
        return survivors

    def _screen_batch(self, batch: list[NewsItem], echo: Callable[[str], None] = print) -> list[NewsItem]:
        try:
            out = self.run({"items": batch})
        except Exception as ex:
            echo(f"[monitor] 初筛批次失败（本批全保留）: {type(ex).__name__}: {str(ex)[:60]}")
            return batch  # 宽松降级：筛不动就全留，细评兜底
        keep_idx: set[int] = set()
        dup_primary: dict[int, int] = {}
        for k in out.keep:
            if not (1 <= k.index <= len(batch)):
                continue  # 越界丢弃（LLM 索引幻觉）
            keep_idx.add(k.index - 1)
            if k.dup_of is not None and 1 <= k.dup_of <= len(batch):
                dup_primary[k.index - 1] = k.dup_of - 1
        result: list[NewsItem] = []
        for i in sorted(keep_idx):
            primary = dup_primary.get(i)
            # dup 副本仅在主条也被保留时才跳过；主条未保留/指向自身 → 独立保留
            if primary is not None and primary != i and primary in keep_idx:
                continue
            result.append(batch[i])
        return result
