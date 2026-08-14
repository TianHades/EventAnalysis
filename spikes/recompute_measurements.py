"""一次性重算：修复旧实现产出的度量与归因（零 LLM 调用）。

背景（2026-08 代码审查）：
- 旧 max_drawdown 只在累计 AR 全局最低点取值，V 型后回落场景系统性低估 → 全量重算
- 旧行业 CAR 用 ffill/bfill 把行业基准冻结外推到真实覆盖范围外（伪造基准）→ 全量重算
- 旧池统计 t 检验跑在"三窗取最大"上 → 改固定 CAR[0,+20] 口径后重算 attribution
- measurements 表 append-only 累积了重复行（归因取最旧行）→ 先删后插顺带去重

保持保真：候选/方向/元数据沿用库内最新一行；行情与基准走磁盘缓存；
报告仅重渲染模板段，保留 LLM 生成的 ## 1 摘要与 ## 4 判读。

用法：
  PYTHONPATH=src python spikes/recompute_measurements.py            # dry-run：只打印 diff
  PYTHONPATH=src python spikes/recompute_measurements.py --apply    # 备份 DB 后写库
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter  # noqa: E402
from eventreplay.adapters.stock_master import StockMaster  # noqa: E402
from eventreplay.adapters.tushare_source import TushareSource  # noqa: E402
from eventreplay.measurement.event_study import measure_stock  # noqa: E402
from eventreplay.measurement.report_template import render_report, report_filename  # noqa: E402
from eventreplay.pipeline.nodes.attribution import AttributionNode  # noqa: E402
from eventreplay.schema import ImpactMeasurement  # noqa: E402
from eventreplay.storage import Storage  # noqa: E402

DB_PATH = "data/eventreplay.db"
REPORTS_DIR = Path("reports")


def load_old_rows(conn: sqlite3.Connection, replay_id: str) -> dict[str, ImpactMeasurement]:
    """每个 stock_id 取最新一行（旧实现 append-only 会产生重复行）。"""
    rows = conn.execute(
        "SELECT payload FROM measurements WHERE replay_id=? ORDER BY id", (replay_id,)
    ).fetchall()
    by_stock: dict[str, ImpactMeasurement] = {}
    for (payload,) in rows:
        m = ImpactMeasurement.model_validate_json(payload)
        by_stock[m.stock_id] = m  # 后行覆盖 → 保留最新
    return by_stock


def extract_report_sections(text: str) -> tuple[str | None, str | None] | None:
    """解析旧报告的 LLM 段（## 1 摘要、## 4 判读）；返回 None 表示结构不符、跳过重渲染。"""
    if "## 7 局限" not in text:  # replay 模板签名（前瞻报告无此节）
        return None
    if "本报告由 Python 模板生成" in text:  # 模板兜底版：无 LLM 段
        return ("", "")
    lines = text.split("\n")

    def find(prefix: str) -> int:
        return next((i for i, l in enumerate(lines) if l.startswith(prefix)), -1)

    i1, i2, i4, i5 = find("## 1"), find("## 2"), find("## 4"), find("## 5")
    if min(i1, i2, i4, i5) < 0 or not (i1 < i2 < i4 < i5):
        return None
    return ("\n".join(lines[i1:i2]).rstrip(), "\n".join(lines[i4:i5]).rstrip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run）")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    storage = Storage(args.db)
    qa = AkshareQuoteAdapter()
    sm = StockMaster()
    ts = TushareSource()
    attr_node = AttributionNode(None)

    replay_ids = [r[0] for r in conn.execute("SELECT DISTINCT replay_id FROM measurements ORDER BY replay_id")]
    print(f"[recompute] {len(replay_ids)} 个 replay，模式={'APPLY' if args.apply else 'DRY-RUN'}")

    if args.apply:
        bak = f"{args.db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(args.db, bak)
        print(f"[recompute] DB 已备份 → {bak}")

    # 汇总 diff
    stat = Counter()
    mdd_deltas: list[float] = []
    ind_flips_v2n = Counter()  # 行业 CAR value→None（旧实现伪造基准的窗口）
    ind_changed: list[float] = []
    pool_stat_changes: list[str] = []
    dropped_log: list[str] = []

    for n, rid in enumerate(replay_ids, 1):
        event = storage.get_event(rid)
        if event is None:
            print(f"[{n}/{len(replay_ids)}] {rid}: events 表缺失，跳过")
            stat["events_missing"] += 1
            continue
        old = load_old_rows(conn, rid)
        rd = event.reaction_date
        start, end = rd - timedelta(days=350), rd + timedelta(days=200)
        try:
            bench = qa.index_daily("sh000300", start, end)
        except Exception as e:
            print(f"[{n}] {rid}: 基准拉取失败 {type(e).__name__}，跳过")
            stat["bench_fail"] += 1
            continue

        episode_id = event.episodes[0].id
        new_ms: list[ImpactMeasurement] = []
        for code, old_m in old.items():
            resolved = sm.resolve(code)
            if resolved is None or not sm.is_real_at(code, rd):
                reason = "resolve失败" if resolved is None else "事件日不在市（退市/未上市）"
                dropped_log.append(f"{rid}/{code}: {reason}")
                stat["dropped"] += 1
                continue
            try:
                quotes = qa.daily_adjusted(code, start, end)
            except Exception as e:
                dropped_log.append(f"{rid}/{code}: 行情拉取失败 {type(e).__name__}")
                stat["dropped"] += 1
                continue
            bench_ind = None
            l1 = ts.classify_l1(code)
            if l1:
                ic = ts.industry_index_codes().get(l1)
                if ic:
                    bench_ind = ts.fetch_industry_index_daily(ic)
            m = measure_stock(
                stock_id=code,
                episode_id=episode_id,
                quotes=quotes,
                bench=bench,
                reaction_date=rd,
                board=resolved.board,
                is_st=resolved.is_st,
                listing_date=resolved.listing_date,
                direction=old_m.direction,
                bench_industry=bench_ind,
            )
            if m is None:
                dropped_log.append(f"{rid}/{code}: 度量返回 None（数据不足/新股）")
                stat["dropped"] += 1
                continue
            # 元数据沿用旧值（候选来源、行业标签不重算 LLM）
            m.chain_node_id = old_m.chain_node_id
            m.rationale = old_m.rationale
            m.stock_name = old_m.stock_name or resolved.name
            m.sw_industry_l3 = old_m.sw_industry_l3
            new_ms.append(m)

            # diff 统计
            stat["pairs"] += 1
            if old_m.max_drawdown is not None and m.max_drawdown is not None:
                d = m.max_drawdown - old_m.max_drawdown
                if abs(d) > 1e-9:
                    stat["mdd_changed"] += 1
                    mdd_deltas.append(d)
            for w in ("car_industry_0_20", "car_industry_0_60", "car_industry_0_90"):
                ov, nv = getattr(old_m, w), getattr(m, w)
                if ov is not None and nv is None:
                    ind_flips_v2n[w] += 1
                elif ov is not None and nv is not None and abs(nv - ov) > 1e-6:
                    ind_changed.append(nv - ov)
            for w in ("car_0_20", "car_0_60", "car_0_90"):  # 市场 CAR 应当不变（sanity）
                ov, nv = getattr(old_m, w), getattr(m, w)
                if (ov is None) != (nv is None) or (ov is not None and nv is not None and abs(nv - ov) > 1e-6):
                    stat["market_car_drift"] += 1

        # 归因重算（固定窗口口径）+ tier 回填
        cands = [{"stock_id": c, "direction": m.direction} for c, m in old.items() if m.direction is not None]
        new_ar = attr_node.run({"measurements": new_ms, "candidates": cands})
        tier_map = {pm.stock_id: pm.tier for pm in new_ar.beneficiary_pool + new_ar.victim_pool}
        new_ms = [m.model_copy(update={"tier": tier_map.get(m.stock_id, m.tier)}) for m in new_ms]

        old_ar_row = conn.execute("SELECT payload FROM attribution_results WHERE replay_id=?", (rid,)).fetchone()
        if old_ar_row:
            old_ar = json.loads(old_ar_row["payload"])
            for key in ("beneficiary", "victim"):
                o = (old_ar.get("pool_stats") or {}).get(key) or {}
                nw = (new_ar.pool_stats or {}).get(key)
                o_n, n_n = o.get("n"), (nw.n if nw else 0)
                o_p, n_p = o.get("grank_p"), (nw.grank_p if nw else None)
                if o_n != n_n or (o_p is None) != (n_p is None) or (o_p is not None and n_p is not None and abs(o_p - n_p) > 1e-6):
                    pool_stat_changes.append(
                        f"{rid}/{key}: n {o_n}→{n_n}, p {o_p if o_p is None else round(o_p,4)}→{n_p if n_p is None else round(n_p,4)}"
                    )
        stat["events_ok"] += 1
        stat["stocks_before"] += len(old)
        stat["stocks_after"] += len(new_ms)

        # 报告重渲染（保留 LLM ## 1 / ## 4 段）
        report_path = REPORTS_DIR / f"{report_filename(event.description, str(event.announcement_date))}.md"
        report_note = "报告文件不存在"
        if report_path.exists():
            sections = extract_report_sections(report_path.read_text(encoding="utf-8"))
            if sections is None:
                report_note = "结构不符，保留旧报告"
                stat["report_skipped"] += 1
            else:
                summary_md, analysis_md = sections
                chain = storage.get_chain(rid)
                md = render_report(
                    event=event, chain=chain, attribution=new_ar, measurements=new_ms,
                    summary_md=summary_md or None, analysis_md=analysis_md or None,
                )
                report_note = "重渲染"
                stat["report_rewritten"] += 1
                if args.apply:
                    report_path.write_text(md, encoding="utf-8")

        if args.apply:
            conn.execute("DELETE FROM measurements WHERE replay_id=?", (rid,))
            conn.commit()
            for m in new_ms:
                storage.save_measurement(m, rid)
            storage.save_attribution(new_ar, rid)
            chain = storage.get_chain(rid)
            if chain is not None:
                chain = chain.model_copy(update={
                    "beneficiary_stock_ids": [p.stock_id for p in new_ar.beneficiary_pool],
                    "victim_stock_ids": [p.stock_id for p in new_ar.victim_pool],
                })
                storage.save_chain(chain)
            for line in dropped_log:
                if line.startswith(rid):
                    stock_code = line.split("/")[1].split(":")[0]
                    storage.append_skip(replay_id=rid, stock_id=stock_code, episode_id=episode_id,
                                        reason=line.split(":", 1)[1].strip())
        print(f"[{n}/{len(replay_ids)}] {rid}: {len(old)}→{len(new_ms)} 标的，{report_note}")

    conn.close()
    storage.close()

    # ---- 汇总 ----
    print("\n===== 汇总 =====")
    print(f"事件: ok={stat['events_ok']} 缺失={stat['events_missing']} 基准失败={stat['bench_fail']}")
    print(f"标的: {stat['stocks_before']}→{stat['stocks_after']}（丢弃 {stat['dropped']}，含 is_real_at 新过滤）")
    print(f"max_drawdown: {stat['mdd_changed']}/{stat['pairs']} 对发生变化"
          + (f"，|Δ| 均值 {sum(abs(d) for d in mdd_deltas)/len(mdd_deltas):.4f}，最大 {min(mdd_deltas):.4f}" if mdd_deltas else ""))
    print(f"行业CAR value→None（揭穿伪造基准）: {dict(ind_flips_v2n) or '无'}")
    if ind_changed:
        print(f"行业CAR 数值变化: {len(ind_changed)} 个，|Δ| 均值 {sum(abs(d) for d in ind_changed)/len(ind_changed):.4f}")
    print(f"市场CAR sanity 偏移（应为 0）: {stat['market_car_drift']}")
    print(f"池统计变化: {len(pool_stat_changes)} 个" + (f"；示例 {pool_stat_changes[:5]}" if pool_stat_changes else ""))
    print(f"报告: 重渲染 {stat['report_rewritten']}，保留旧 {stat['report_skipped']}")
    if dropped_log:
        print(f"丢弃明细（前 10）: {dropped_log[:10]}")
    if not args.apply:
        print("\n[DRY-RUN] 未写库。确认无误后加 --apply 执行。")


if __name__ == "__main__":
    main()
