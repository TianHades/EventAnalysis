"""报告模板生成器（ReportAssembly LLM 超时时的确定性兜底）。

从结构化数据（event/chain/attribution/measurements）生成 markdown，秒级可靠。
不如 LLM 报告丰富（无归纳性叙述），但保证报告永远能产出。
"""

from __future__ import annotations

from ..schema import AttributionResult, Event, ImpactMeasurement, TransmissionChain

# 合规披露（全项目唯一来源）：复盘/前瞻报告正文末尾统一追加，推送卡片底部备注同义改写。
DISCLAIMER = (
    "> **研究用途，非投资建议**：本报告由 EventAnalysis 自动生成，仅供研究参考，"
    "不构成任何投资建议；历史规律不代表未来表现，数据可能存在延迟或误差。"
)


def _fmt(v: float | None) -> str:
    return f"{v:+.1%}" if v is not None else "-"


def report_filename(desc: str, date_str: str) -> str:
    """事件描述 + 日期 → 合法报告文件名（不含 .md 后缀）。

    如 '2016-02-04_国务院发布钢铁行业化解过剩产'。供 orchestrator/forward_orchestrator 复用。
    """
    import re

    safe = re.sub(r'[\\/:*?"<>|\n\r\t]', "", desc[:30]).strip()
    return f"{date_str}_{safe}" if safe else date_str


def _short_title(desc: str, max_len: int = 40) -> str:
    """智能截断标题（不截断词中间）。"""
    if len(desc) <= max_len:
        return desc
    cut = desc[:max_len]
    # 在最近的标点或空格处截断
    for sep in ("，", "。", "；", "、", " "):
        idx = cut.rfind(sep)
        if idx > max_len // 2:
            return cut[:idx]
    return cut


def render_report(
    *,
    event: Event,
    chain: TransmissionChain | None,
    attribution: AttributionResult,
    measurements: list[ImpactMeasurement],
    summary_md: str | None = None,
    analysis_md: str | None = None,
) -> str:
    lines: list[str] = []
    title = _short_title(event.description)
    lines.append(f"# {title} 复盘报告\n")
    if summary_md is None and analysis_md is None:
        lines.append("> ⚠️ 本报告由 Python 模板生成（LLM 报告节点超时兜底）；数据完整，缺 LLM 归纳叙述。\n")

    # 1 事件摘要（LLM 归纳优先，自带标题；兜底用结构化字段）
    if summary_md:
        lines.append(summary_md.rstrip())
    else:
        lines.append("## 1 事件摘要")
        lines.append(f"- 描述：{event.description}")
        lines.append(f"- 公告日 / 反应日：{event.announcement_date} / {event.reaction_date}")
        sd = event.shock_dimension.value if event.shock_dimension else "?"
        idir = event.impact_direction.value if event.impact_direction else "?"
        lines.append(f"- 类型：{event.event_type} | 冲击维度：{sd} | 方向：{idir}")
    lines.append("")

    # 2 产业链传导
    lines.append("## 2 产业链传导")
    if chain and chain.nodes:
        low_ratio = chain.low_confidence_ratio
        if low_ratio > 0.5:
            lines.append(f"> ⚠️ 产业链推理置信度低（{low_ratio:.0%} 节点低置信）\n")
        for n in chain.nodes:
            lc = " [低置信]" if n.low_confidence else ""
            sd2 = n.shock_direction.value if n.shock_direction else "?"
            lines.append(f"- {n.level}·{n.label}（{n.sw_industry_l1 or '?'}, {sd2}）{lc}")
        # narrative 可能含 mermaid——直接输出（不包额外代码围栏）
        # 注意：chain.narrative 实际 2000-4000 字（含 ①②③④ 完整叙事），过小截断会砍掉 ③/④
        if chain.narrative:
            lines.append("")
            lines.append(chain.narrative[:6000])
    lines.append("")

    # 3 标的表（受益/受损主表 + 方向冲突子表）
    # 多 episode 同标的：取 |car_0_20| 最大的度量为代表（与 impact 排序/attribution
    # 首见去重同口径——原来 dict 后写覆盖是"末个 episode 胜"，口径互相矛盾）
    meas_by_id: dict = {}
    for m in measurements:
        cur = meas_by_id.get(m.stock_id)
        if cur is None or abs(m.car_0_20 or 0.0) > abs(cur.car_0_20 or 0.0):
            meas_by_id[m.stock_id] = m
    lines.append("## 3 受益/受损标的（按 |CAR[0,+90]| 排序）\n")

    _HEADER = "| 代码 | 名称 | 行业 | CAR(市场)+20 | +60 | +90 | CAR(行业)+20 | +60 | +90 | 持续性比 | 分级 | 核心逻辑 |"
    _SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|"

    def _render_member_table(members: list, *, approx_zero_note: bool) -> None:
        lines.append(_HEADER)
        lines.append(_SEP)
        rows = []
        for m in members:
            ms = meas_by_id.get(m.stock_id)
            rows.append((m, ms, ms.car_0_90 if ms else None))
        rows.sort(key=lambda x: abs(x[2] or 0), reverse=True)
        for m, ms, _ in rows:
            persist = f"{ms.persistence_ratio_60_5:.2f}" if ms and ms.persistence_ratio_60_5 is not None else "-"
            if approx_zero_note and ms and ms.car_0_5 is not None and abs(ms.car_0_5) < 0.005:
                persist += "(≈0)"
            rat = (m.rationale or ms.rationale if ms else "") or "—"
            rat = rat[:50].replace("|", "/")
            ind = ms.sw_industry_l3 if ms and ms.sw_industry_l3 else "—"
            lines.append(
                f"| {m.stock_id} | {ms.stock_name or '—' if ms else '—'} | {ind} | {_fmt(ms.car_0_20 if ms else None)} | "
                f"{_fmt(ms.car_0_60 if ms else None)} | {_fmt(ms.car_0_90 if ms else None)} | "
                f"{_fmt(ms.car_industry_0_20 if ms else None)} | {_fmt(ms.car_industry_0_60 if ms else None)} | "
                f"{_fmt(ms.car_industry_0_90 if ms else None)} | {persist} | {m.tier.value} | {rat} |"
            )
        lines.append("")

    for pool_name, pool in [("受益", attribution.beneficiary_pool), ("受损", attribution.victim_pool)]:
        normal = [m for m in pool if not m.direction_conflict]
        conflicted = [m for m in pool if m.direction_conflict]

        lines.append(f"### {pool_name}（{len(normal)}）")
        _render_member_table(normal, approx_zero_note=True)

        if conflicted:
            lines.append(f"#### ⚠️ {pool_name}方向冲突（{len(conflicted)}）")
            _render_member_table(conflicted, approx_zero_note=False)

    # 4 逻辑链判读分析（LLM 增量，自带标题；兜底时缺省）
    if analysis_md:
        lines.append(analysis_md.rstrip())
        lines.append("")

    # 5 组合统计
    lines.append("## 5 组合统计（披露）")
    for key in ("beneficiary", "victim"):
        stat = attribution.pool_stats.get(key)  # type: ignore[arg-type]
        if stat and stat.disclosed:
            fb = (
                f"，另有 {stat.fallback_n} 只弱反应 fallback 仅列示未计入"
                if getattr(stat, "fallback_n", 0)
                else ""
            )
            lines.append(
                f"- {key}: N={stat.n}, CAAR={stat.caar:+.2%}, "
                f"t={stat.grank_z:.2f}(p={stat.grank_p:.3f}) [简化t检验]{fb}"
            )
    lines.append("")

    # 5 双层 CAR 判读说明
    lines.append("## 6 双层 CAR 判读指南")
    lines.append("")
    lines.append("| CAR(市场) | CAR(行业) | 含义 |")
    lines.append("|---|---|---|")
    lines.append("| >0 | >0（差值≤10pct）| 个股独立 α 强（跑赢市场且跑赢 peers）|")
    lines.append("| >0 | ≈0 | 搭板块便车（跟 peers 持平，上涨来自板块）|")
    lines.append("| >0 | <0 | 板块涨了但个股跑输 peers（落后标的）|")
    lines.append("| <0 | \\|CAR行业\\|>\\|CAR市场\\| | peer 池极端落后标的（**禁止解读为抗跌**）|")
    lines.append("| <0 | ≈0 | 随板块下跌 |")
    lines.append("| 任意 | =0 | 无可比 peer 池（行业超额不可比）|")
    lines.append("")

    # 6 局限
    lines.append("## 7 局限与数据说明")
    if summary_md is None and analysis_md is None:
        lines.append("- 报告由 Python 模板生成（LLM 报告节点超时兜底）；无 LLM 归纳叙述")
    else:
        lines.append("- ## 1 摘要 + ## 4 判读由 LLM 生成，其余由结构化模板渲染")
    lines.append("- CAR(市场) 基准为沪深300价格指数（含 β/分红偏差）；CAR(行业) = 行业指数市场模型（R_stock = α+β·R_ind 估计窗拟合）残差累计")
    lines.append("- ST 状态按当前名单判定，历史 ST 戴/摘帽期涨跌停天数可能失真")
    lines.append("- 行业标签为申万三级（L3），部分标的 L3 未命中时降级为 L1")
    lines.append("- 组合统计量 = 横截面 CAR[0,+20] 简化 t 检验（未做聚集调整、横截面相关修正）")
    lines.append(f"- 度量标的 {len(measurements)} 只；CAR[0,+90] 窗口约 4.5 个月，可能混入其他事件")
    lines.append("- 方向冲突标的（⚠️）= 候选方向与实际 CAR 符号相反，解读以市场实际定价为准")
    peaks = [m.peak_ar for m in measurements if m.peak_ar is not None]
    troughs = [m.trough_ar for m in measurements if m.trough_ar is not None]
    if troughs:  # 有谷值数据时峰值/谷值都展示（受损标的看谷值）
        p_txt = f"{max(peaks):+.1%}" if peaks else "-"
        lines.append(f"- 事件窗 AR 峰值/谷值（全池极值）：峰值最高 {p_txt}，谷值最低 {min(troughs):+.1%}")

    # 合规披露：复盘报告唯一出口（LLM 成功路径与模板兜底路径都经 render_report）→ 末尾统一追加
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
