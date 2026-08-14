"""一次性脚本：生成 2021 年「外部系统性冲击」候选事件清单。

聚焦政策/地缘/经济/流动性/产业等外部冲击对 A 股的传导。2021 主线：政策大年（双减+反垄断+恒大+碳中和）。
web 核实受阻（bing 污染），基于知识（高置信，著名事件）。
用法：PYTHONPATH=src python spikes/build_mining_2021.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "中共中央办公厅+国务院办公厅发布《双减意见》，全面规范校外学科类培训，教培行业系统性重塑",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2021-07-24", "key_entities": ["双减", "校外培训", "教育"],
        "industries": ["教育", "在线教育", "传媒"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 5,
        "rationale": "双减政策彻底重塑教培行业，教育股暴跌（新东方-90%/好未来-95%），A股教培/在线教育毁灭性打击",
    },
    {
        "description": "市场监管总局对阿里巴巴作出行政处罚，罚款182.28亿元（中国反垄断史上最大罚单）",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2021-04-10", "key_entities": ["阿里巴巴", "反垄断", "市场监管总局", "182亿"],
        "industries": ["互联网平台", "电商", "互联网金融"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "阿里182亿反垄断罚单（史上最大），互联网平台监管收紧，中概互联持续承压（全年）",
    },
    {
        "description": "A股核心资产（茅指数）2月见顶回落，消费/医药龙头估值泡沫破裂，蓝筹白马全年熊市",
        "event_type": "估值修正", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2021-02-10", "key_entities": ["核心资产", "茅指数", "估值泡沫"],
        "industries": ["食品饮料", "医药", "消费", "蓝筹白马"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "核心资产（茅台/海天/恒瑞等）估值见顶回落，茅指数全年-30%，蓝筹白马系统性杀估值",
    },
    {
        "description": "恒大集团全面爆发流动性危机（财富兑付逾期+美元债违约），房地产信用风险系统性蔓延",
        "event_type": "金融风险", "shock_dimension": "情绪", "impact_direction": "利空",
        "announcement_date": "2021-09-08", "key_entities": ["恒大", "房地产", "信用风险"],
        "industries": ["房地产", "建材", "银行", "信托"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "恒大危机（万亿负债）爆发，地产信用风险蔓延，房地产/建材/银行承压（三道红线+销售下行）",
    },
    {
        "description": "国务院发布《2030碳达峰行动方案》，新能源/光伏/锂电确立为国家级产业主线",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2021-10-24", "key_entities": ["碳达峰", "碳中和", "新能源"],
        "industries": ["光伏", "锂电池", "新能源车", "储能"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 4,
        "rationale": "碳达峰行动方案落地，新能源（宁德/隆基/阳光）全年主线，光伏/锂电/储能产业爆发",
    },
    {
        "description": "大宗商品暴涨引发国常会'保供稳价'（铜/煤/钢创历史新高），周期股过山车",
        "event_type": "供给侧事件", "shock_dimension": "供给", "impact_direction": "利好",
        "announcement_date": "2021-05-19", "key_entities": ["大宗商品", "保供稳价", "国常会"],
        "industries": ["钢铁", "煤炭", "有色金属", "化工"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "大宗暴涨（铜/煤/钢创新高）→ 5月国常会保供稳价（行政干预）→ 周期股暴涨暴跌过山车",
    },
    {
        "description": "全国大范围限电（能耗双控+煤价暴涨+电荒），制造业产能受限",
        "event_type": "供给侧事件", "shock_dimension": "供给", "impact_direction": "利空",
        "announcement_date": "2021-09-23", "key_entities": ["限电", "能耗双控", "电荒"],
        "industries": ["制造业", "化工", "纺织", "光伏（上游硅料）"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "限电潮波及全国（东北尤为严重），制造业产能受限（化工/纺织受损，部分高耗能产品涨价受益）",
    },
    {
        "description": "美联储FOMC正式宣布Taper（缩减购债），全球流动性收紧预期启动",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2021-11-03", "key_entities": ["美联储", "Taper", "缩减购债"],
        "industries": ["黄金", "成长股", "新兴市场"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "联储正式宣布Taper，全球流动性收紧预期，黄金/成长股承压，新能源主线情绪扰动",
    },
    {
        "description": "滴滴出行APP被下架（数据安全审查），互联网平台数据安全监管升级",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2021-07-04", "key_entities": ["滴滴", "数据安全", "网络安全审查"],
        "industries": ["互联网平台", "网络安全", "数据安全"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "滴滴下架（网络安全审查），互联网平台监管升级（反垄断+数据安全双重收紧）",
    },
    {
        "description": "Facebook更名为Meta all-in元宇宙，全球元宇宙概念股爆发",
        "event_type": "技术突破", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2021-10-28", "key_entities": ["Facebook", "Meta", "元宇宙"],
        "industries": ["传媒", "游戏", "VR/AR", "消费电子"],
        "significance": 2, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 4, "impact_scale": 2,
        "rationale": "Meta改名引爆元宇宙主题，A股传媒/游戏/VR概念短期暴涨（题材驱动）",
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
    path = Path("data/mining_candidates_2021-01-01_2021-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
