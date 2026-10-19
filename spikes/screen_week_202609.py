# -*- coding: utf-8 -*-
"""2026-09-07 ~ 2026-09-14 一周大事件筛选 spike（v2：财新回溯 + 实时源 + 搜索锚定补充）。

数据源（CCTV news_cctv 本环境每请求 63s 反爬延迟判死，弃用）：
1. 财新 stock_news_main_cx 100 条：URL 含日期（database.caixin.com/YYYY-MM-DD/），可回溯过滤整周
2. 实时源当前批次：财联社 / 金十 / 韭研（覆盖今日）
3. Chrome+Baidu 搜索锚定的补充事件（本周候选框架，见 EXTRA_EVENTS）
漏斗：dedup_by_title → NewsScreeningNode 批量初筛（haiku）→ EventExtractor 并发细评
输出：data/screening_week_2026-09-07_2026-09-14.json + 控制台汇总表。
"""
from _loadenv import *  # noqa: F401

import sys
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eventreplay.adapters.news_adapter import (
    AkshareNewsAdapter, CLSNewsAdapter, Jin10NewsAdapter, JiuyanNewsAdapter,
)
from eventreplay.llm import ClaudeCodeProvider
from eventreplay.pipeline.nodes.event_extractor import EventExtractor
from eventreplay.pipeline.nodes.news_screening import NewsScreeningNode, dedup_by_title
from eventreplay.schema.news import NewsItem

START = date(2026, 9, 29)
END = date(2026, 9, 29)
MODEL = "haiku"
WORKERS = 3
MAX_PER_SOURCE = 60
OUT = Path(__file__).resolve().parents[1] / "data" / f"screening_week_{START}_{END}.json"

# 检索锚定的今日候选事件（Chrome+Baidu 2026-09-29 检索核实，剔除 2024 同期旧闻）；
# 交由 EventExtractor 独立细评打分，宁多勿漏。
EXTRA_EVENTS = [
    NewsItem(
        id="extra-cny-4arrows", source="web检索补充",
        title="央行四箭齐发：PSL利率下调0.25个百分点并扩大支持领域，科创技改再贷款+2000亿，支农支小再贷款+5000亿",
        content=(
            "9月29日央行结构性货币政策密集落地：下调抵押补充贷款（PSL）利率0.25个百分点；扩大PSL支持领域；"
            "增加科技创新和技术改造再贷款额度2000亿元；增加支农支小再贷款额度5000亿元。"
            "与财政部购房贴息新政同日落地，形成'货币端+财政端'组合发力，市场解读为'精准滴灌+需求托底'。"
            "政策性金融指向基建'六张网'、城中村改造、科技扩产等方向。"
        ),
        published_at=datetime(2026, 9, 29, 9, 0),
    ),
    NewsItem(
        id="extra-mof-house-subsidy", source="web检索补充",
        title="财政部等三部门：10月1日起对新发放商业性个人住房贷款购房实施贴息",
        content=(
            "9月29日财政部会同央行、金融监管总局发布通知：10月1日起对居民使用新发放商业性个人住房贷款购房"
            "实施财政贴息，以降低居民购房付息负担（热点答问称最高可减少付息近5万元）。"
            "当日申万房地产指数上涨4.06%，自9月18日以来累计涨幅约10%——板块事前已在交易贴息预期，"
            "存在'利好落地'的预期差风险。"
        ),
        published_at=datetime(2026, 9, 29, 10, 0),
    ),
    NewsItem(
        id="extra-us30y-high", source="web检索补充",
        title="美国30年期国债收益率升至5.57%创2002年以来新高，10年期触及5.27%为2007年6月以来最高",
        content=(
            "北京时间9月28日晚，美国30年期国债收益率一度上涨7个基点报5.57%，刷新2002年以来新高；"
            "10年期美债收益率盘中最高触及5.27%，刷新2007年6月以来最高。长端无风险利率锚持续上移"
            "压制全球风险资产估值，对高估值成长板块与港股流动性构成压力。"
        ),
        published_at=datetime(2026, 9, 29, 7, 0),
    ),
    NewsItem(
        id="extra-sasac-155", source="web检索补充",
        title="央企'十五五'规划正式印发：2030年战略性新兴产业增加值占比再提升5个百分点，'六张网'建设扩内需",
        content=(
            "9月28日国新办发布会，国资委副主任庞骁刚介绍中央企业发展'十五五'规划已正式印发，"
            "一体谋划'十五五'发展目标方向路径，明确到2030年央企战略性新兴产业增加值占比再提升5个百分点，"
            "'六张网'建设（现代化基础设施网络）是'十五五'扩大有效投资的重要抓手。"
            "央企科技重估、基建与战新产业方向受益。"
        ),
        published_at=datetime(2026, 9, 29, 8, 0),
    ),
    NewsItem(
        id="extra-china-us-trade", source="web检索补充",
        title="中美经贸磋商达成多项共识；国常会部署加大宏观逆周期调节",
        content=(
            "9月29日财经早餐要点：中美经贸磋商达成多项共识（热搜'三张照片见证中美关系的刻度'呼应）；"
            "国常会部署加大宏观逆周期调节；八部门出台服务业金融支持政策。"
            "外部经贸关系边际缓和叠加国内政策加码，风险偏好修复。"
        ),
        published_at=datetime(2026, 9, 29, 8, 30),
    ),
    NewsItem(
        id="extra-cxmt-dram", source="web检索补充",
        title="长鑫科技扩产存储（DRAM），国产存储产业链景气上行",
        content=(
            "9月29日报道：长鑫科技扩产存储芯片产能，国产DRAM产业链（存储芯片、封测、设备材料）"
            "受益于AI驱动存储需求与国产替代双主线。存储涨价周期叠加扩产资本开支，"
            "产业链公司订单预期上修。"
        ),
        published_at=datetime(2026, 9, 29, 11, 0),
    ),
    NewsItem(
        id="extra-gac-toyota", source="web检索补充",
        title="广汽集团拟收购一汽丰田股权，汽车央企整合预期升温",
        content=(
            "9月29日财经早餐：广汽集团拟收购一汽丰田股权，汽车央国企整合重组预期升温；"
            "同日中际旭创高额分红落地、中信证券高管换届。汽车行业格局重塑关注整车央企平台价值重估。"
        ),
        published_at=datetime(2026, 9, 29, 9, 30),
    ),
    NewsItem(
        id="extra-hfc-ipo", source="web检索补充",
        title="鸿富诚创业板上市首日大涨680%，AI高功率芯片导热材料景气印证",
        content=(
            "9月29日鸿富诚登陆创业板，开盘报600元较76.86元发行价上涨680.64%，中一签浮盈约26.16万元，"
            "年内发行价第二高的创业板新股。公司主营AI高功率芯片导热界面材料，"
            "2025年营收同比+114%、净利润同比+277%。AI硬件散热材料景气度与打新情绪高涨的印证。"
        ),
        published_at=datetime(2026, 9, 29, 10, 30),
    ),
    NewsItem(
        id="extra-ashare-close", source="web检索补充",
        title="A股节前缩量收涨：沪指+0.18%站上3816点，地产/传媒/固态电池强势，石化/煤炭/海运回调",
        content=(
            "9月29日A股三大指数震荡收涨：上证+0.18%、深成指+0.34%、创业板指+0.09%，市场缩量运行"
            "（国庆长假前倒数第二个交易日），57只涨停/11只跌停。房地产、传媒、固态电池强势；"
            "石油化工、煤炭、海运等周期板块回调（美伊冲突溢价回落）。物业概念、快手概念、AI语料概念活跃，"
            "新华传媒6连板。央行当日开展905亿7天逆回购+6985亿隔夜逆回购呵护节前流动性。"
            "明日（9/30）将公布9月PMI数据。"
        ),
        published_at=datetime(2026, 9, 29, 15, 30),
    ),
]


def caixin_in_window() -> list[NewsItem]:
    """财新 100 条 → 从 URL 提取日期（database.caixin.com/YYYY-MM-DD/）过滤窗口。"""
    got = AkshareNewsAdapter().fetch_latest()
    kept: list[NewsItem] = []
    for it in got:
        m = re.search(r"/(\d{4}-\d{2}-\d{2})/", it.url or "")
        if not m:
            continue  # 无日期 URL 交由后续源处理，不混入
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if START <= d <= END:
            kept.append(it.model_copy(update={"published_at": datetime(d.year, d.month, d.day, 12, 0)}))
    print(f"[collect] 财新: 拉到 {len(got)} 条，窗口内 {len(kept)} 条", flush=True)
    return kept[:MAX_PER_SOURCE]


def realtime_sources() -> list[NewsItem]:
    out: list[NewsItem] = []
    for name, adapter in [
        ("财联社", CLSNewsAdapter()),
        ("金十", Jin10NewsAdapter()),
        ("韭研", JiuyanNewsAdapter()),
    ]:
        try:
            got = adapter.fetch_latest()
        except Exception as ex:
            print(f"[collect] {name} 拉取失败: {type(ex).__name__}: {str(ex)[:60]}", flush=True)
            continue
        kept = [it for it in got if it.published_at is None or it.published_at.date() >= START]
        print(f"[collect] {name}: 拉到 {len(got)} 条，窗口内 {len(kept)} 条", flush=True)
        out.extend(kept[:MAX_PER_SOURCE])
    return out


def extract_one(extractor: EventExtractor, item: NewsItem):
    """单条细评；失败返回 None 不中断（照抄 MonitoringPusher._extract_one 模式）。"""
    try:
        return extractor.run({"news": item, "recent_context": ""})
    except Exception as ex:
        print(f"[extract] 失败 {item.title[:30]}: {type(ex).__name__}: {str(ex)[:60]}", flush=True)
        return None


def cand_to_dict(c) -> dict:
    e = c.event
    return {
        "description": e.description,
        "event_type": e.event_type,
        "shock_dimension": e.shock_dimension.value if e.shock_dimension else None,
        "impact_direction": e.impact_direction.value if e.impact_direction else None,
        "announcement_date": str(e.announcement_date),
        "reaction_date": str(e.reaction_date),
        "announcement_time": e.announcement_time.value if e.announcement_time else None,
        "key_entities": e.key_entities,
        "industries": c.industries,
        "significance": c.significance,
        "a_share_relevance": c.a_share_relevance,
        "surprise_timing": c.surprise_timing,
        "surprise_magnitude": c.surprise_magnitude,
        "impact_scale": c.impact_scale,
    }


def main() -> None:
    print(f"[screen] 窗口 {START} ~ {END}，模型 {MODEL}", flush=True)
    items = caixin_in_window() + realtime_sources() + EXTRA_EVENTS
    print(f"[screen] 合计 {len(items)} 条（财新回溯 + 实时 + 搜索补充 {len(EXTRA_EVENTS)}）", flush=True)
    items = dedup_by_title(items)
    print(f"[screen] 标题去重后 {len(items)} 条", flush=True)

    reasoner = ClaudeCodeProvider(model_alias=MODEL)
    survivors = NewsScreeningNode(reasoner).screen(items, echo=lambda s: print(s, flush=True))
    print(f"[screen] 初筛后 {len(survivors)} 条", flush=True)

    extractor = EventExtractor(reasoner)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda it: extract_one(extractor, it), survivors))
    cands = [c for c in results if c is not None]
    print(f"[screen] 细评后事件 {len(cands)} 条", flush=True)

    # 聚合去重：EventExtractor 稳定 id = description md5
    by_id: dict[str, object] = {}
    for c in cands:
        by_id.setdefault(c.event.id, c)
    merged = list(by_id.values())

    def sort_key(c):
        surprise = (c.surprise_timing + c.surprise_magnitude) / 2
        return (-c.significance, -surprise, -c.impact_scale, -c.a_share_relevance)

    merged.sort(key=sort_key)

    print("\n===== 一周大事件筛选结果（按 重大性/突发·超预期/影响规模 排序）=====", flush=True)
    for i, c in enumerate(merged, 1):
        surprise = (c.surprise_timing + c.surprise_magnitude) / 2
        print(
            f"\n[{i}] {c.event.description[:70]}\n"
            f"    {c.event.announcement_date} {c.event.announcement_time or ''} | "
            f"{c.event.event_type} | 维度={c.event.shock_dimension.value if c.event.shock_dimension else '?'} | "
            f"方向={c.event.impact_direction.value if c.event.impact_direction else '?'}\n"
            f"    sig={c.significance} a_share={c.a_share_relevance} "
            f"surprise={surprise:.1f}(t{c.surprise_timing}/m{c.surprise_magnitude}) impact={c.impact_scale}\n"
            f"    行业: {','.join(c.industries[:6])}",
            flush=True,
        )

    OUT.write_text(
        json.dumps([cand_to_dict(c) for c in merged], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[done] {len(merged)} 事件已落盘 {OUT}", flush=True)


if __name__ == "__main__":
    main()
