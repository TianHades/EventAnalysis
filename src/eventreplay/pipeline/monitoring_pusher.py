"""MonitoringPusher：采集 → 抽取 → 高分 forward → 推送（Phase 4 功能1）。"""

from __future__ import annotations

from .event_dedup import EventDedup
from .push_channel import PushChannel


class MonitoringPusher:
    """单次：fetch_latest → EventExtractor → 阈值/去重过滤 → forward → 推送。

    定时循环由 CLI（monitor 命令）用 while+sleep 驱动；本类只做单次 run_once。
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
        """从 seen_events 表拉取最近识别的事件，构建上下文摘要（校准 surprise 打分）。"""
        try:
            rows = self.dedup.storage._conn.execute(
                "SELECT payload FROM seen_events WHERE purpose='push' ORDER BY seen_at DESC LIMIT 10"
            ).fetchall()
        except Exception:
            return ""
        lines = []
        for r in rows:
            try:
                import json
                data = json.loads(r[0]) if r[0] else {}
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
        items = self.news.fetch_latest()
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
            for ch in self.channels:
                try:
                    if ch.push(title=cand.event.description[:40], markdown=md, event=cand):
                        pushed_ok = True
                except Exception:
                    pass  # 单渠道失败不影响其他渠道
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
