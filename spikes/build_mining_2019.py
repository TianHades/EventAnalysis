"""一次性脚本：生成 2019 年「外部系统性冲击」候选事件清单。

聚焦政策/地缘/经济/流动性等外部冲击对 A 股的传导。2019 主线：中美贸易战反复 + 科创板/5G + A股春季躁动。
web 核实受阻（bing 污染），基于知识（高置信，著名事件）。
用法：PYTHONPATH=src python spikes/build_mining_2019.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "中美第十一轮贸易磋商谈崩，美方将2000亿美元中国商品关税从10%升至25%，贸易战急剧升级",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2019-05-10", "key_entities": ["美国", "中国", "贸易战", "2000亿关税"],
        "industries": ["出口制造", "半导体", "机械设备", "纺织", "农业"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "贸易战急剧升级（2000亿10%→25%），A股5月暴跌，出口/半导体/机械承压，反制预期利好农业/稀土",
    },
    {
        "description": "美国商务部将华为及70家关联企业列入实体清单，禁止美企向华为出售技术产品，半导体卡脖子升级",
        "event_type": "地缘政治", "shock_dimension": "情绪", "impact_direction": "利空",
        "announcement_date": "2019-05-16", "key_entities": ["美国商务部", "华为", "实体清单", "半导体"],
        "industries": ["通信", "半导体", "电子", "国产替代"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "华为实体清单升级卡脖子，5G/半导体短期承压+国产替代长期主线催化",
    },
    {
        "description": "央行全面降准1个百分点（2019首次），叠加政策暖风，A股Q1春季躁动大涨约30%",
        "event_type": "政策转向", "shock_dimension": "流动性", "impact_direction": "利好",
        "announcement_date": "2019-01-04", "key_entities": ["央行", "降准", "春季躁动"],
        "industries": ["全市场", "券商", "成长股"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "1月降准+政策底，Q1春季躁动大涨~30%（券商/成长领涨），全年估值修复起点",
    },
    {
        "description": "科创板正式开市（首批25家公司上市），注册制试点落地，硬科技估值锚定",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2019-07-22", "key_entities": ["科创板", "注册制", "硬科技"],
        "industries": ["半导体", "生物医药", "高端制造", "券商"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "科创板开市+注册制落地，硬科技估值锚定+券商投行受益，A股注册制元年",
    },
    {
        "description": "美联储8月开启预防式降息（年内3次降息，从2018加息周期转为降息周期）",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利好",
        "announcement_date": "2019-08-01", "key_entities": ["美联储", "鲍威尔", "预防式降息"],
        "industries": ["黄金", "新兴市场", "成长股"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "联储从加息转降息（3次），全球流动性转松，黄金大涨+成长股估值修复",
    },
    {
        "description": "在岸人民币兑美元汇率突破7.0关口（2008年以来首次），汇率心理防线失守",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2019-08-05", "key_entities": ["人民币", "破7", "汇率"],
        "industries": ["航空", "造纸", "出口", "黄金"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "人民币破7（2008以来首次），航空/造纸（美元负债）承压，出口/黄金受益",
    },
    {
        "description": "工信部向三大运营商发放5G商用牌照，中国正式进入5G商用元年",
        "event_type": "技术突破", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2019-06-06", "key_entities": ["工信部", "5G商用牌照", "三大运营商"],
        "industries": ["通信", "半导体", "消费电子", "物联网"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "5G商用牌照发放，5G建设主线启动，通信/消费电子/物联网受益（全年主题）",
    },
    {
        "description": "非洲猪瘟持续蔓延叠加猪周期，猪肉价格全年暴涨（CPI破3），养殖产业链景气",
        "event_type": "供给冲击", "shock_dimension": "供给", "impact_direction": "利好",
        "announcement_date": "2019-03-15", "key_entities": ["非洲猪瘟", "猪肉价格", "养殖"],
        "industries": ["生猪养殖", "饲料", "动物疫苗"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "非洲猪瘟+猪周期，猪价暴涨（CPI破3），生猪养殖龙头量价齐升（牧原/温氏）",
    },
    {
        "description": "中美达成第一阶段贸易协议（取消部分关税+农产品采购+知识产权），贸易战阶段性缓和",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2019-12-13", "key_entities": ["中美", "第一阶段贸易协议"],
        "industries": ["出口制造", "半导体", "电子", "农产品"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "一阶段协议达成，贸易战阶段性缓和，出口链修复+市场情绪改善（2019年尾回升）",
    },
    {
        "description": "沙特阿美石油设施遭无人机袭击（Abqaiq/Khurais），全球原油供给瞬时减少5%",
        "event_type": "地缘军事", "shock_dimension": "供给", "impact_direction": "利好",
        "announcement_date": "2019-09-14", "key_entities": ["沙特阿美", "也门胡塞", "原油"],
        "industries": ["石油石化", "油服", "新能源替代"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "沙特油田被袭，油价瞬时暴涨，石化/油服短期受益+新能源替代逻辑",
    },
]


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def build() -> dict:
    final = []
    for e in EVENTS:
        dk = make_dedup_key(e["event_type"], e["announcement_date"], e["key_entities"], description=e["description"])
        final.append({
            "dedup_key": dk, "description": e["description"], "event_type": e["event_type"],
            "shock_dimension": e["shock_dimension"], "impact_direction": e["impact_direction"],
            "announcement_date": e["announcement_date"], "key_entities": e["key_entities"],
            "industries": e["industries"],
            "significance": _clamp(e["significance"], 1, 4), "a_share_relevance": _clamp(e["a_share_relevance"], 1, 5),
            "surprise_timing": _clamp(e["surprise_timing"], 1, 5), "surprise_magnitude": _clamp(e["surprise_magnitude"], 1, 5),
            "impact_scale": _clamp(e["impact_scale"], 1, 5), "source": "llm_knowledge",
        })
    return {"final": final, "llm_rejected": [], "process_summary": {
        "raw_count": len(EVENTS), "final": len(final),
        "method": "人工搜集+知识（web核实受阻），聚焦外部系统性冲击",
    }}


if __name__ == "__main__":
    out = build()
    path = Path("data/mining_candidates_2019-01-01_2019-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
