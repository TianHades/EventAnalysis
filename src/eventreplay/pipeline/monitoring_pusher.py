"""MonitoringPusher：采集 → 抽取 → 高分 forward → 推送（Phase 4 功能1）。"""

from __future__ import annotations

import json
import time
from typing import Callable

from .event_dedup import EventDedup
from .push_channel import PushChannel

# 上下文闭环：识别但未达推送阈值的事件也记入 seen（purpose='identified'），
# _build_recent_context 同时读 push + identified，供后续轮次 surprise 校准。
_CONTEXT_PURPOSES = ("push", "identified")
_IDENTIFIED_MIN_SIG = 2  # significance ≥ 2 的事件才值得进入上下文（1=噪音）


def run_monitor_loop(
    pusher: MonitoringPusher,
    *,
    interval_min: int,
    echo: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    **run_once_kwargs,
) -> None:
    """监控主循环：单轮任何异常只 echo 不退出（监控系统的核心要求是持续运行）。

    KeyboardInterrupt（Ctrl-C）不捕获，向上传播由 CLI 处理。
    """
    while True:
        try:
            pusher.run_once(echo=echo, **run_once_kwargs)
        except Exception as ex:
            echo(f"[monitor] 本轮执行异常（已跳过，继续运行）: {type(ex).__name__}: {str(ex)[:120]}")
        echo(f"[monitor] sleep {interval_min}min... (Ctrl-C 停止)")
        sleep(interval_min * 60)


class MonitoringPusher:
    """单次：fetch_latest → EventExtractor → 阈值/去重过滤 → forward → 推送。

    定时循环由 run_monitor_loop（CLI monitor 命令）用 while+sleep 驱动；本类只做单次 run_once。
    """

    def __init__(
        self,
        news_adapter,
        event_extractor,
        forward_orchestrator,
        dedup: EventDedup,
        channels: list[PushChannel],
    ) -> None:
        self.news = news_adapter
        self.extractor = event_extractor
        self.forward = forward_orchestrator
        self.dedup = dedup
        self.channels = channels

    def _build_recent_context(self, echo=print) -> str:
        """从 seen_events 表拉取最近识别/推送的事件，构建上下文摘要（校准 surprise 打分）。"""
        try:
            rows: list[dict] = []
            for purpose in _CONTEXT_PURPOSES:
                rows.extend(self.dedup.storage.list_recent_seen(purpose, limit=10))
            rows.sort(key=lambda r: r.get("seen_at") or "", reverse=True)
        except Exception:
            return ""
        lines = []
        for r in rows:
            try:
                data = json.loads(r.get("payload") or "")
                desc = data.get("description", data.get("event", {}).get("description", ""))
                if desc:
                    lines.append(f"- {desc[:60]}")
            except Exception:
                continue
        return "\n".join(lines[:8]) if lines else ""

    def run_once(self, significance_min: int = 3, a_share_min: int = 4, surprise_min: float = 0, impact_min: int = 0, echo=print) -> int:
        """单次采集→抽取→高分 forward→推送；返回推送数。

        surprise_min：(surprise_timing+surprise_magnitude)/2 的下限，0=不过滤。
        impact_min：影响规模下限，0=不过滤。

        无高分事件触发时，自动输出本轮最重要的事件摘要（让用户感知市场动态）。
        """
        try:
            items = self.news.fetch_latest()
        except Exception as ex:
            echo(f"[monitor] 新闻采集失败: {type(ex).__name__}: {str(ex)[:120]}")
            return 0
        echo(f"[monitor] 采集 {len(items)} 条新闻")

        # 构建近期事件上下文（校准 surprise 打分）
        recent_ctx = self._build_recent_context(echo=echo)
        if recent_ctx:
            echo(f"[monitor] 近期上下文：{recent_ctx[:80]}...")

        pushed = 0
        # 记录本轮所有识别到的事件（供无触发时摘要 + 上下文累积）
        all_cands: list = []
        for item in items:
            try:
                cand = self.extractor.run({"news": item, "recent_context": recent_ctx})
            except Exception as ex:
                echo(f"[monitor] 抽取失败 {item.title[:30]}: {str(ex)[:60]}")
                continue
            if cand is None:
                continue  # 非事件（噪音过滤）
            all_cands.append(cand)
            if cand.significance >= _IDENTIFIED_MIN_SIG:
                # 上下文闭环：识别到的事件（含未达推送阈值的）记入 identified，
                # 供后续轮次 surprise 校准（不 purpose='push'，不影响推送重试）
                self.dedup.mark_seen([cand], purpose="identified")
            if cand.significance < significance_min or cand.a_share_relevance < a_share_min:
                continue  # 不够重要
            if surprise_min > 0:
                _surprise = (cand.surprise_timing + cand.surprise_magnitude) / 2
                if _surprise < surprise_min:
                    continue  # 突发性/超预期性不足（预期内事件可能"利好出尽"）
            if impact_min > 0 and cand.impact_scale < impact_min:
                continue  # 影响规模不足
            if self.dedup.is_seen(cand.dedup_key, purpose="push"):
                continue  # 已推送/已处理
            try:
                md, _ = self.forward.forward(
                    description=cand.event.description,
                    date_str=str(cand.event.reaction_date),
                    mode="pre",
                    event_id=f"fwd-{cand.dedup_key[:8]}",
                )
            except Exception as ex:
                echo(f"[monitor] forward 失败 {cand.event.description[:30]}: {str(ex)[:60]}")
                continue  # forward 失败不标记 seen，下次重试

            # 拼接事件评分到报告头部（让飞书卡片显示各维度评估结论）
            _surprise_avg = (cand.surprise_timing + cand.surprise_magnitude) / 2
            score_block = (
                f"**事件评分**｜重大性 **{cand.significance}**/4 ｜"
                f"A股可达 **{cand.a_share_relevance}**/5 ｜"
                f"突发 **{cand.surprise_timing}**/5 ｜超预期 **{cand.surprise_magnitude}**/5 ｜"
                f"影响 **{cand.impact_scale}**/5 ｜超预期均值 **{_surprise_avg:.1f}**\n\n"
            )
            md = score_block + md

            pushed_ok = False
            results: list[tuple[PushChannel, bool]] = []
            for ch in self.channels:
                try:
                    results.append((ch, bool(ch.push(title=cand.event.description[:40], markdown=md, event=cand))))
                except Exception:
                    results.append((ch, False))  # 单渠道失败不影响其他渠道
            # 推送成功判定只看实时渠道：archive 渠道（FileChannel 落盘）恒成功，
            # 若计入会让"全失败不标 seen"的重试设计被架空；仅当只配了 archive 时以其为准
            realtime = [(ch, ok) for ch, ok in results if not getattr(ch, "is_archive", False)]
            pushed_ok = any(ok for _, ok in (realtime or results))
            if pushed_ok:
                self.dedup.mark_seen([cand], purpose="push")
                pushed += 1
                echo(f"[monitor] 推送 {cand.event.description[:30]} (sig={cand.significance})")
            else:
                echo(f"[monitor] 推送全失败 {cand.event.description[:30]}，不标记 seen，下次重试")

        # 无高分事件触发 → 输出本轮最重要事件摘要
        if pushed == 0 and all_cands:
            top = max(all_cands, key=lambda c: (c.significance, c.a_share_relevance, c.impact_scale))
            echo(
                f"[monitor] 本轮无触发前瞻的事件（阈值 sig>={significance_min} a>={a_share_min} "
                f"surprise>={surprise_min}）。"
                f"最高分事件：sig={top.significance} a={top.a_share_relevance} "
                f"imp={top.impact_scale} | {top.event.description[:50]}"
            )

        echo(f"[monitor] 本次推送 {pushed} 条（共识别 {len(all_cands)} 个事件）")
        return pushed
