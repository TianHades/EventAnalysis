"""一次性脚本：生成 2024 年「外部系统性冲击」候选事件清单。

2024 主线：924政策大转向（A股暴涨）+ 新国九条 + 量化微盘崩 + 517地产 + 联储降息 + 特朗普。
用法：PYTHONPATH=src python spikes/build_mining_2024.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "央行+金管局+证监会三部门发布会推出'924组合拳'（降准0.5%+降息+创设股票回购增持再贷款+地产支持），A股暴涨",
        "event_type": "政策转向", "shock_dimension": "流动性", "impact_direction": "利好",
        "announcement_date": "2024-09-24", "key_entities": ["央行", "924组合拳", "再贷款", "A股暴涨"],
        "industries": ["券商", "全市场", "房地产", "消费"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 5,
        "rationale": "924组合拳（降准+降息+股票回购增持再贷款+地产），A股5日暴涨25%（2770→3674），券商涨停潮",
    },
    {
        "description": "国务院发布新'国九条'《关于加强监管防范化解风险推动资本市场高质量发展的若干意见》",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2024-04-12", "key_entities": ["国九条", "资本市场", "强监管"],
        "industries": ["券商", "蓝筹白马", "红利"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "新国九条（资本市场高质量发展），强监管+退市+分红，红利低波+蓝筹白马主线催化",
    },
    {
        "description": "量化微盘股踩踏式崩盘（DMA/雪球集中敲入+流动性危机），中证2000单周暴跌13%",
        "event_type": "流动性事件", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2024-02-05", "key_entities": ["微盘股", "量化", "DMA", "雪球敲入"],
        "industries": ["微盘股", "量化", "中小盘"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "微盘股流动性危机（DMA去杠杆+雪球敲入），中证2000单周-13%，量化基金集中赎回",
    },
    {
        "description": "央行推出517地产组合拳（取消房贷利率下限+降首付15%+3000亿保障性住房再贷款收储）",
        "event_type": "政策转向", "shock_dimension": "需求", "impact_direction": "利好",
        "announcement_date": "2024-05-17", "key_entities": ["517新政", "房贷利率", "收储"],
        "industries": ["房地产", "建材", "家居", "银行"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "517地产组合拳（史上最大力度），取消利率下限+降首付+收储再贷款，地产链修复预期",
    },
    {
        "description": "日本央行加息15bp+缩表预期，日元套息交易逆转引发全球股灾（日经8/5单日-12%）",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2024-07-31", "key_entities": ["日本央行", "加息", "日元套息", "全球股灾"],
        "industries": ["全市场", "成长股", "半导体"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "日元 Carry Trade 逆转（全球套息平仓），日经-12%+全球股灾，A股短期跟跌（8/5）",
    },
    {
        "description": "美联储9月FOMC降息50基点（2020年来首次降息+超预期幅度），全球宽松周期开启",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利好",
        "announcement_date": "2024-09-18", "key_entities": ["美联储", "降息50bp", "鲍威尔"],
        "industries": ["黄金", "成长股", "新兴市场"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "联储首降50bp（超预期幅度），全球宽松开启，黄金创新高+A股流动性环境改善（924前奏）",
    },
    {
        "description": "特朗普在宾州竞选集会遭枪击（右耳受伤），'战斗'照片全球传播，选情剧变",
        "event_type": "地缘政治", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2024-07-13", "key_entities": ["特朗普", "枪击", "美国大选"],
        "industries": ["军工", "黄金", "比特币"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 2,
        "rationale": "特朗普遇刺（黑天鹅），选情剧变（特朗普当选概率飙升），避险+军工+加密货币催化",
    },
    {
        "description": "中共二十届三中全会召开，提出全面深化改革+新质生产力+统一大市场",
        "event_type": "政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2024-07-15", "key_entities": ["三中全会", "新质生产力", "改革"],
        "industries": ["新质生产力", "科技", "先进制造"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "三中全会确立全面深化改革+新质生产力主线，科技/先进制造长期方向（预期内事件）",
    },
    {
        "description": "特朗普赢得2024年美国总统大选（共和党全面掌控），中美贸易战2.0预期升温",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2024-11-06", "key_entities": ["特朗普", "美国大选", "关税"],
        "industries": ["出口制造", "半导体", "纺服", "农业"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "特朗普当选（全面对华关税预期60%），出口链承压+农业反制受益+半导体管制延续",
    },
    {
        "description": "DeepSeek发布V3/R1系列开源大模型（性能对标GPT-4o+训练成本仅557万美元），AI算力逻辑重构",
        "event_type": "技术突破", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2024-12-26", "key_entities": ["DeepSeek", "V3", "开源", "AI"],
        "industries": ["AI", "软件", "半导体", "算力"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "DeepSeek V3/R1（低成本高性能开源），'算力平权'冲击英伟达逻辑，A股AI应用/软件受益",
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
        "method": "人工搜集+知识，聚焦外部系统性冲击",
    }}


if __name__ == "__main__":
    out = build()
    path = Path("data/mining_candidates_2024-01-01_2024-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
