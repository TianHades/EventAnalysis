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

START = date(2026, 9, 7)
END = date(2026, 9, 14)
MODEL = "haiku"
WORKERS = 3
MAX_PER_SOURCE = 60
OUT = Path(__file__).resolve().parents[1] / "data" / f"screening_week_{START}_{END}.json"

# 搜索锚定的本周候选事件（Chrome+Baidu 2026-09-14 检索，日期来自报道线索）；
# 交由 EventExtractor 独立细评打分，宁多勿漏。
EXTRA_EVENTS = [
    NewsItem(
        id="extra-us-ir-oil", source="web检索补充",
        title="美伊冲突升级互袭油轮，胡塞武装袭击沙特本土，油价逼近100美元",
        content=(
            "9月6日前后美伊在海湾地区对抗升级：美军摧毁伊朗油轮并打击霍尔木兹海峡附近拉腊克岛目标，"
            "伊朗武装力量打击约旦和阿联酋境内美军目标，胡塞武装施压红海航线并对沙特实施海上禁运。"
            "两大关键航道通行同步收紧，供应中断担忧升温，国际油价一度逼近100美元/桶，"
            "9月7日周一A股开盘受冲击后震荡，上证指数全周收于3930点附近，通信、军工领涨。"
            "油运（绕行拉长运距）、石油石化、军工受益；航空、炼化成本承压。"
        ),
        published_at=datetime(2026, 9, 7, 8, 0),
    ),
    NewsItem(
        id="extra-fed-sep-hike", source="web检索补充",
        title="美联储9月加息预期升温至约90%（预防式加息），此前8月非农远超预期",
        content=(
            "9月4日公布的美国8月非农就业数据远超预期，市场对美联储9月加息定价快速升温至约90%，"
            "主流机构定性为'预防式加息'。美联储9月15-16日召开议息会议，北京时间9月17日02:00公布决议。"
            "过去一周油价上行叠加加息预期推升美债收益率，全球风险资产情绪受扰动，"
            "机构普遍认为议息落地、海外不确定性出清后A股有望开启修复行情。"
        ),
        published_at=datetime(2026, 9, 8, 8, 0),
    ),
    NewsItem(
        id="extra-csrc-155", source="web检索补充",
        title="证监会明确'十五五'资本市场改革方向：A股打造为境内优质企业上市首选地，健全长钱长投机制",
        content=(
            "证监会明确'十五五'资本市场改革方向，首次提出把A股打造为境内优质企业上市首选地，"
            "健全'长钱长投'机制；年内社保、年金、保险等中长期资金净买入超6000亿元。"
            "券商、金融IT直接受益于制度红利叙事。"
        ),
        published_at=datetime(2026, 9, 9, 8, 0),
    ),
    NewsItem(
        id="extra-hainan-rain", source="web检索补充",
        title="海南持续强降雨：南海热带低压+冷空气，防汛防风应急响应升至二级，全省转移超4.2万人",
        content=(
            "受南海热带低压和冷空气共同影响，海南多地连续强降雨，9月13日11时海南省将防汛防风应急响应"
            "提升至二级，截至14日全省已转移危险区域群众超4.2万人，万宁出现龙卷风（EF0级），"
            "两地升级发布暴雨红色预警。海南本地基建、农业、旅游相关标的短期扰动。"
        ),
        published_at=datetime(2026, 9, 13, 12, 0),
    ),
    NewsItem(
        id="extra-health-plan", source="web检索补充",
        title="'未来五年'民生健康重磅规划发布",
        content=(
            "本周热搜'未来五年民生健康重磅规划'：涉及民生健康领域的五年规划文件发布，"
            "创新药、医疗器械、医疗服务等大健康产业链预期受益。具体文号与发布时点以官方为准。"
        ),
        published_at=datetime(2026, 9, 10, 8, 0),
    ),
    NewsItem(
        id="extra-uk-split", source="web检索补充",
        title="英国'要分家'：苏格兰独立议题再起（热搜）",
        content=(
            "本周中文热搜'英国要分家了吗'：英国国内分离议题（苏格兰独立等）再度升温。"
            "对A股直接影响有限，主要通过英镑、英债与全球风险偏好间接传导；具体触发事件与时点待核实。"
        ),
        published_at=datetime(2026, 9, 12, 8, 0),
    ),
    NewsItem(
        id="extra-ru-ua", source="web检索补充",
        title="俄乌战线升温：俄军对乌大规模打击，乌国家安全局总部遭无人机袭击",
        content=(
            "9月4日俄罗斯国防部通报：俄军过去一周对乌克兰军事目标实施1次大规模打击和17次集群打击，"
            "控制哈尔科夫、顿涅茨克等地15个居民点；泽连斯基称位于基辅市中心的乌国家安全局总部"
            "遭俄军无人机袭击。战线升温于9月7日当周继续影响军工、能源与粮食贸易链条定价。"
        ),
        published_at=datetime(2026, 9, 7, 8, 0),
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
