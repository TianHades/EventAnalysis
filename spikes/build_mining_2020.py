"""一次性脚本：生成 2020 年「外部系统性冲击」候选事件清单。

聚焦政策/地缘/经济/流动性/公共卫生等外部冲击对 A 股的传导。2020 主线：新冠疫情 + 全球放水 + 7月牛市。
web 核实受阻（bing 污染），基于知识（高置信，著名事件）。
用法：PYTHONPATH=src python spikes/build_mining_2020.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "钟南山确认新冠病毒人传人，武汉1月23日封城，新冠疫情正式成为系统性公共卫生冲击",
        "event_type": "公共卫生", "shock_dimension": "情绪", "impact_direction": "利空",
        "announcement_date": "2020-01-20", "key_entities": ["新冠肺炎", "钟南山", "武汉封城"],
        "industries": ["医药生物", "防护物资", "航空", "零售", "旅游"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 5,
        "rationale": "百年一遇公共卫生冲击，A股春节后暴跌8%，医药/防护受益，线下消费/交运/旅游重创",
    },
    {
        "description": "美联储紧急降息至零+启动无限量QE（COVID-19全球大流行引发流动性危机）",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利好",
        "announcement_date": "2020-03-23", "key_entities": ["美联储", "无限QE", "零利率"],
        "industries": ["黄金", "成长股", "消费", "全市场"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "联储无限QE救市，全球流动性泛滥，成长股/黄金/消费受益，A股Q2起结构牛起点",
    },
    {
        "description": "沙特与俄罗斯石油价格战崩溃，OPEC+减产谈判破裂，布伦特原油单日暴跌30%",
        "event_type": "供给侧事件", "shock_dimension": "供给", "impact_direction": "利空",
        "announcement_date": "2020-03-09", "key_entities": ["沙特", "俄罗斯", "OPEC+", "原油"],
        "industries": ["石油石化", "油服", "新能源替代"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "油价单日-30%（历史罕见），叠加疫情需求萎缩，石化/油服重创，新能源替代逻辑反向催化",
    },
    {
        "description": "A股7月突然放量暴涨（券商涨停潮+北向涌入），市场交易'牛市来了'预期",
        "event_type": "情绪事件", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2020-07-06", "key_entities": ["A股", "券商涨停潮", "牛市"],
        "industries": ["券商", "金融", "低估值蓝筹"],
        "significance": 3, "a_share_relevance": 5, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "7月券商涨停潮+成交量破1.5万亿，低估值蓝筹补涨（券商/银行/地产），牛市预期情绪驱动",
    },
    {
        "description": "创业板注册制改革正式落地（首批18家上市，存量股票涨跌幅放宽至20%）",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2020-08-24", "key_entities": ["创业板", "注册制", "20%涨跌幅"],
        "industries": ["创业板", "券商", "科技创新"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "创业板注册制落地+20%涨跌幅，存量改革深化，券商/创业板科技创新受益",
    },
    {
        "description": "蚂蚁集团A+H股IPO被紧急叫停（上市前夕被暂缓），金融科技监管收紧",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2020-11-03", "key_entities": ["蚂蚁集团", "金融科技", "监管"],
        "industries": ["互联网金融", "互联网平台", "金融科技"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "蚂蚁IPO暂缓（史上最大IPO被叫停），互联网金融监管收紧，互金/平台经济估值重估",
    },
    {
        "description": "辉瑞/BioNTech宣布新冠疫苗三期有效率95%，全球复苏预期骤升",
        "event_type": "公共卫生", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2020-11-09", "key_entities": ["辉瑞", "新冠疫苗", "复苏预期"],
        "industries": ["航空", "旅游", "能源", "顺周期"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "疫苗95%有效（远超预期），全球复苏预期骤升，顺周期（航空/旅游/能源）修复行情",
    },
    {
        "description": "十九届五中全会通过十四五规划建议，确立科技自立+碳中和+双循环战略方向",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2020-10-29", "key_entities": ["十四五规划", "碳中和", "科技自立"],
        "industries": ["新能源", "光伏", "半导体", "军工"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 4,
        "rationale": "十四五确立碳中和+科技自立+双循环，新能源/光伏/半导体/军工成为2021主线（全年产业方向）",
    },
    {
        "description": "RCEP（区域全面经济伙伴关系协定）正式签署，全球最大自贸区诞生",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2020-11-15", "key_entities": ["RCEP", "东盟", "自贸区"],
        "industries": ["纺织", "轻工", "跨境电商", "港口航运"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 2,
        "rationale": "RCEP签署（全球最大自贸区），纺织/轻工/跨境电商受益，亚太贸易格局重塑",
    },
    {
        "description": "拜登赢得美国总统大选（民主党重返白宫），中美关系预期重构",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "混合",
        "announcement_date": "2020-11-07", "key_entities": ["拜登", "美国大选", "中美关系"],
        "industries": ["新能源", "半导体", "出口制造"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 3, "impact_scale": 2,
        "rationale": "拜登当选，新能源/多边主义预期利好，但半导体管制延续+加税预期不确定性",
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
    path = Path("data/mining_candidates_2020-01-01_2020-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
