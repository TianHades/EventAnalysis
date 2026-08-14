"""一次性脚本：生成 2022 年「外部系统性冲击」候选事件清单。

2022 主线：俄乌战争 + 美联储激进加息 + 疫情封控 + 地产暴雷 + 中美脱钩。
web 核实受阻，基于知识（高置信，著名事件）。
用法：PYTHONPATH=src python spikes/build_mining_2022.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "俄罗斯对乌克兰发动特别军事行动，俄乌战争全面爆发，全球地缘政治格局剧变",
        "event_type": "地缘军事", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2022-02-24", "key_entities": ["俄罗斯", "乌克兰", "北约"],
        "industries": ["军工", "黄金", "石油石化", "农产品", "天然气"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "俄乌战争爆发（二战后欧洲最大军事冲突），避险+能源+粮食+军工多主线，A股短期急跌后反弹",
    },
    {
        "description": "美联储3月开启加息周期（年内连续7次加息425基点，40年最激进），全球流动性急剧收缩",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2022-03-16", "key_entities": ["美联储", "加息", "鲍威尔"],
        "industries": ["黄金", "成长股", "新兴市场", "科技"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "联储年内加息425bp（40年最激进），全球资产重定价，A股/港股外资流出，成长股杀估值",
    },
    {
        "description": "上海疫情封城（浦东全面封控+全域静态管理），全球供应链二次冲击",
        "event_type": "公共卫生", "shock_dimension": "供给", "impact_direction": "利空",
        "announcement_date": "2022-03-28", "key_entities": ["上海", "封城", "供应链"],
        "industries": ["汽车", "半导体", "消费电子", "物流", "零售"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "上海封城（全球供应链核心节点），汽车/半导体/消费电子产业链断供，A股4月暴跌",
    },
    {
        "description": "美联储6月加息75基点（1994年来最大单次幅度），通胀失控引发全球衰退预期",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2022-06-15", "key_entities": ["美联储", "75基点", "通胀"],
        "industries": ["黄金", "铜", "成长股", "新兴市场"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "联储75bp（1994年来最大），大宗暴跌（铜-20%），全球衰退预期，A股成长股承压",
    },
    {
        "description": "佩洛西窜访台湾，中美台海军事对峙升温（解放军围岛军演）",
        "event_type": "地缘政治", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2022-08-02", "key_entities": ["佩洛西", "台湾", "中美", "军演"],
        "industries": ["军工", "半导体", "黄金"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "佩洛西窜台引发台海危机，解放军围岛军演，军工催化+半导体自主可控+避险情绪",
    },
    {
        "description": "美国出台《芯片与科学法案》（527亿美元补贴+对华半导体出口管制升级）",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2022-08-09", "key_entities": ["芯片法案", "美国", "半导体", "出口管制"],
        "industries": ["半导体", "半导体设备", "芯片设计"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "芯片法案+出口管制升级，半导体卡脖子加剧（设备/EDA/高端芯片），国产替代长期主线",
    },
    {
        "description": "中共二十大召开，确立中国式现代化+安全发展+科技自立战略方向",
        "event_type": "政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2022-10-16", "key_entities": ["二十大", "中国式现代化", "安全发展"],
        "industries": ["军工", "半导体", "信创", "高端制造"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "二十大确立安全发展主线，军工/半导体/信创/高端制造成为2023主线起点（信创行情）",
    },
    {
        "description": "防疫政策重大调整（二十条+新十条优化防控），消费复苏预期骤升",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2022-12-07", "key_entities": ["新十条", "防疫优化", "消费复苏"],
        "industries": ["消费", "旅游", "航空", "医药", "零售"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "新十条防疫优化（放开预期），消费/旅游/航空修复行情，医药（发热药品）短期爆发",
    },
    {
        "description": "房地产'金融16条'出台+房企融资'三支箭'，地产信用风险政策托底",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2022-11-23", "key_entities": ["金融16条", "三支箭", "房地产"],
        "industries": ["房地产", "建材", "银行", "家居"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "金融16条+三支箭（信贷/债券/股权融资），地产信用风险政策托底，地产链修复预期",
    },
    {
        "description": "中概股审计监管达成合作协议（PCAOB进场检查），中概股退市风险阶段性解除",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2022-08-26", "key_entities": ["中概股", "PCAOB", "审计监管"],
        "industries": ["互联网平台", "电商", "中概互联"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 2,
        "rationale": "中美审计监管达成协议，中概股退市风险解除，互联网平台估值修复（3月金稳会后持续）",
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
    path = Path("data/mining_candidates_2022-01-01_2022-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
