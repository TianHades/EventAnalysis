"""一次性脚本：2017 候选（跳过已完成的雄安/MSCI + 卡死的环保限产，留剩余 4 个）。

雄安/MSCI 已入库；环保限产 candidate_generation 卡死（待单独排查）。重跑剩余 4：
地条钢/317调控/金融去杠杆/资管新规。
用法：PYTHONPATH=src python spikes/build_mining_2017.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "上半年全面取缔'地条钢'（6月30日大限），供给侧改革钢铁去产能深化，约1.4亿吨地条钢产能出清",
        "event_type": "供给侧事件", "shock_dimension": "供给", "impact_direction": "利好",
        "announcement_date": "2017-06-30", "key_entities": ["发改委", "工信部", "钢铁"],
        "industries": ["钢铁", "铁矿石"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 4,
        "rationale": "地条钢清除+供给侧深化，钢铁供给收缩龙头集中度提升，钢企盈利暴增",
    },
    {
        "description": "北京'317'楼市新政——认房又认贷+二套房首付60%，随后多城密集跟进限购限贷",
        "event_type": "政策转向", "shock_dimension": "需求", "impact_direction": "利空",
        "announcement_date": "2017-03-17", "key_entities": ["北京", "楼市调控"],
        "industries": ["房地产", "建材", "家电"],
        "significance": 3, "a_share_relevance": 5, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "史上最严调控开端，地产链预期转弱，系统性需求冲击",
    },
    {
        "description": "银监会部署'三三四十'专项治理（三违反/三套利/四不当/十乱象），金融去杠杆监管风暴启动",
        "event_type": "政策转向", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2017-04-07", "key_entities": ["银监会", "郭树清"],
        "industries": ["银行", "非银金融", "债券"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "金融去杠杆引发5月股债双杀，银行券商承压，流动性系统性收紧",
    },
    {
        "description": "央行等五部委发布《关于规范金融机构资产管理业务的指导意见》征求意见稿，打破刚兑+净值化+去嵌套，金融去杠杆深化",
        "event_type": "政策转向", "shock_dimension": "流动性", "impact_direction": "利空",
        "announcement_date": "2017-11-17", "key_entities": ["央行", "资管新规"],
        "industries": ["银行", "非银金融", "债券"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "资管新规打破刚兑+净值化，银行理财/非标收缩预期，金融股承压+债市波动",
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
        "method": "2017 剩余4（雄安/MSCI已完成，环保卡死跳过）",
    }}


if __name__ == "__main__":
    out = build()
    path = Path("data/mining_candidates_2017-01-01_2017-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | {c['description'][:30]}")
