"""一次性脚本：生成 2018 年「外部系统性冲击」候选事件清单。

聚焦政策/地缘/经济/流动性等外部冲击对 A 股的传导。2018 主线：中美贸易战 + 股权质押危机 + A股熊市。
web 核实受阻（bing 污染），基于知识（高置信，著名事件）。用户质疑可补充。
用法：PYTHONPATH=src python spikes/build_mining_2018.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "特朗普签署对华贸易总统备忘录，基于301调查对中国600亿美元商品加征关税，中美贸易战正式爆发",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2018-03-23", "key_entities": ["特朗普", "美国", "中国", "301调查"],
        "industries": ["出口制造", "半导体", "机械设备", "农业", "稀土"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 5,
        "rationale": "中美贸易战正式爆发，A股2018年上证-25%核心驱动，出口/半导体/机械承压，农业/稀土反制受益",
    },
    {
        "description": "美国商务部对中兴通讯实施禁售令（7年内禁止美企向中兴出售零部件），半导体卡脖子危机",
        "event_type": "地缘政治", "shock_dimension": "情绪", "impact_direction": "利空",
        "announcement_date": "2018-04-16", "key_entities": ["美国商务部", "中兴通讯", "半导体"],
        "industries": ["通信", "半导体", "电子"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "中兴禁售令暴露半导体卡脖子，5G/芯片国产替代主线催化（贸易战子事件，标的明确）",
    },
    {
        "description": "央行等五部委正式发布《资管新规》（正式稿），打破刚兑+净值化+去嵌套，金融去杠杆落地",
        "event_type": "政策转向", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2018-04-27", "key_entities": ["央行", "资管新规"],
        "industries": ["银行", "非银金融", "债券"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "资管新规正式稿落地（2017征求意见），银行理财/非标收缩，金融股承压+信用债风险",
    },
    {
        "description": "美联储3月加息25基点，2018年首次加息（年内实际加息4次为最频繁一年，缩表加速）",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2018-03-22", "key_entities": ["美联储", "鲍威尔"],
        "industries": ["黄金", "银行", "新兴市场"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "联储2018加息4次（最频）+缩表加速，全球流动性收紧，人民币贬值+新兴市场危机",
    },
    {
        "description": "A股股权质押危机爆发（民企控股股东爆仓），三大报+副总理讲话驰援，多地设立纾困基金",
        "event_type": "金融风险", "shock_dimension": "情绪", "impact_direction": "利空",
        "announcement_date": "2018-10-19", "key_entities": ["股权质押", "民企", "纾困基金"],
        "industries": ["民营企业", "中小盘"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "股权质押爆雷致中小盘暴跌，民企困境，10月19日副总理讲话+纾困基金托底",
    },
    {
        "description": "MSCI正式将中国大盘A股纳入新兴市场指数（首批2.5%，9月升至5%），外资流入A股",
        "event_type": "流动性事件", "shock_dimension": "流动性", "impact_direction": "利好",
        "announcement_date": "2018-06-01", "key_entities": ["MSCI", "A股", "外资"],
        "industries": ["蓝筹白马", "券商", "食品饮料"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "MSCI纳入正式实施（2017宣布），外资流入支撑蓝筹白马（熊市中结构性支撑）",
    },
    {
        "description": "民营经济座谈会召开，重申'两个毫不动摇'+解决民企困难，政策底信号",
        "event_type": "政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2018-11-01", "key_entities": ["民企座谈会", "两个毫不动摇"],
        "industries": ["民营企业", "中小盘"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "民企座谈会重申两个毫不动摇，政策底信号，民企/中小盘情绪修复（股权质押危机后）",
    },
    {
        "description": "个税法修订草案提交（起征点提至5000+专项附加扣除），30年来首次大修",
        "event_type": "政策转向", "shock_dimension": "需求", "impact_direction": "利好",
        "announcement_date": "2018-06-19", "key_entities": ["个税改革", "起征点5000"],
        "industries": ["消费", "零售", "家电"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 2,
        "rationale": "个税起征点5000+专项扣除，居民可支配收入提升，消费板块预期利好",
    },
    {
        "description": "中央宣布支持海南全面深化改革开放，探索建设自由贸易港",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2018-04-13", "key_entities": ["海南", "自由贸易港"],
        "industries": ["海南板块", "基建", "交运", "旅游"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "海南自贸港宣布，海南板块爆发（类似雄安的区域主题），基建/交运/旅游催化",
    },
    {
        "description": "棚改货币化安置收紧（PSL投放收缩），三四线城市房地产红利退潮",
        "event_type": "政策转向", "shock_dimension": "需求", "impact_direction": "利空",
        "announcement_date": "2018-06-30", "key_entities": ["棚改", "PSL", "三四线地产"],
        "industries": ["房地产", "建材", "家电"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "棚改货币化收紧（PSL缩量），三四线地产需求退潮，地产链承压（三四线标的）",
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
    path = Path("data/mining_candidates_2018-01-01_2018-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
