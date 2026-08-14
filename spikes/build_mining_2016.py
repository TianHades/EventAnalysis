"""一次性脚本：生成 2016 年「外部系统性冲击」候选事件清单（mining_candidates 格式）。

定位：聚焦军事/政治/经济/地缘等外部冲击对 A 股的传导（非股市内部制度事件、非个股事件），
契合事件研究法 + 规律库「可类比冲击」价值。日期经 web 搜索核实。

输出：data/mining_candidates_2016-01-01_2016-12-31.json（{final, llm_rejected, process_summary}）
可被 `eventreplay batch-replay --job-file ...` 直接读取。

用法：PYTHONPATH=src python spikes/build_mining_2016.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

# 10 个候选：覆盖 供给/需求/政策/流动性/情绪(地缘) 维度；黑天鹅事件 surprise 高。
# 字段含义见 historical_miner._MINE_SYSTEM：sig(1-4)/a_share(1-5)/surprise_timing(1-5)/
# surprise_magnitude(1-5)/impact_scale(1-5)。
EVENTS = [
    {
        "description": "国务院发布《关于钢铁行业化解过剩产能实现脱困发展的意见》，供给侧改革去产能政策正式落地，钢铁煤炭3-5年压减产能目标明确",
        "event_type": "政策转向",
        "shock_dimension": "供给",
        "impact_direction": "利好",
        "announcement_date": "2016-02-04",
        "key_entities": ["国务院", "国家发改委", "工信部"],
        "industries": ["钢铁", "煤炭", "有色"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 5,
        "rationale": "供给侧改革元年定调，钢铁煤炭去产能3-5亿吨，驱动全年周期股牛市主线，钢价煤价翻倍",
    },
    {
        "description": "AlphaGo 4:1 战胜李世石，人工智能标志性事件，AI 主题催化（对照2023 ChatGPT）",
        "event_type": "技术突破",
        "shock_dimension": "情绪",
        "impact_direction": "利好",
        "announcement_date": "2016-03-09",
        "key_entities": ["Google DeepMind", "李世石"],
        "industries": ["人工智能", "软件"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "AI首次战胜人类围棋世界冠军，技术突破超预期，A股AI概念股主题行情",
    },
    {
        "description": "人民日报刊发「权威人士」访谈，定调经济运行L型、不搞强刺激、全面降杠杆",
        "event_type": "宏观定调",
        "shock_dimension": "政策",
        "impact_direction": "利空",
        "announcement_date": "2016-05-09",
        "key_entities": ["人民日报", "权威人士"],
        "industries": ["周期", "房地产", "基建"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "权威人士三次访谈之一，明确否定大水漫灌，周期地产基建预期逆转短期承压",
    },
    {
        "description": "英国脱欧公投计票结果出炉，全球黑天鹅，避险情绪飙升黄金大涨",
        "event_type": "地缘政治",
        "shock_dimension": "流动性",
        "impact_direction": "利好",
        "announcement_date": "2016-06-24",
        "key_entities": ["英国", "欧盟", "卡梅伦"],
        "industries": ["黄金", "有色"],
        "significance": 4, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 3,
        "rationale": "2016全球最大黑天鹅之一，全球避险资产重定价，A股黄金股受益、短期跟跌后反弹",
    },
    {
        "description": "海牙常设仲裁法院公布南海仲裁案裁决，中美地缘博弈升温，军工板块催化",
        "event_type": "地缘政治",
        "shock_dimension": "情绪",
        "impact_direction": "利好",
        "announcement_date": "2016-07-12",
        "key_entities": ["海牙常设仲裁法院", "菲律宾", "中国"],
        "industries": ["军工", "黄金"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "南海局势升温催化军工行情（招商证券等当时研报印证），地缘危机→军工避险传导",
    },
    {
        "description": "朝鲜进行第五次核试验，东北亚地缘危机升级，军工黄金避险",
        "event_type": "地缘军事",
        "shock_dimension": "情绪",
        "impact_direction": "利好",
        "announcement_date": "2016-09-09",
        "key_entities": ["朝鲜"],
        "industries": ["军工", "黄金"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 4, "impact_scale": 2,
        "rationale": "朝鲜年内第二次核试，突发地缘危机，军工黄金短期避险行情",
    },
    {
        "description": "930北京率先重启楼市限购，随后南京合肥苏州等二十余城密集跟进，房地产去库存转向全面调控",
        "event_type": "政策转向",
        "shock_dimension": "需求",
        "impact_direction": "利空",
        "announcement_date": "2016-09-30",
        "key_entities": ["北京", "南京", "合肥", "苏州"],
        "industries": ["房地产", "建材", "家电"],
        "significance": 4, "a_share_relevance": 5, "surprise_timing": 3, "surprise_magnitude": 4, "impact_scale": 4,
        "rationale": "去库存后骤然转向限购，地产链（地产/建材/家电）预期逆转，系统性需求冲击",
    },
    {
        "description": "特朗普当选美国第45任总统，全球黑天鹅「特朗普交易」，基建减税预期推动有色铜锌铝大涨",
        "event_type": "地缘政治",
        "shock_dimension": "政策",
        "impact_direction": "混合",
        "announcement_date": "2016-11-09",
        "key_entities": ["特朗普", "共和党"],
        "industries": ["有色", "基建", "一带一路"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 4,
        "rationale": "超预期黑天鹅，海外大宗上涨+基建预期传导A股有色，贸易保护主义带来不确定性",
    },
    {
        "description": "OPEC 八年来首次达成减产协议（180万桶/日），油价上行，石化产业链受益",
        "event_type": "供给侧事件",
        "shock_dimension": "供给",
        "impact_direction": "利好",
        "announcement_date": "2016-11-30",
        "key_entities": ["OPEC", "俄罗斯"],
        "industries": ["石油石化", "油服", "化工"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "OPEC八年来首度减产，供给收缩油价上行，A股石化/油服/化工受益",
    },
    {
        "description": "美联储时隔一年宣布加息25基点并暗示2017年加息三次，全球流动性收紧、人民币贬值压力加剧",
        "event_type": "海外宏观",
        "shock_dimension": "流动性",
        "impact_direction": "利空",
        "announcement_date": "2016-12-15",
        "key_entities": ["美联储", "耶伦"],
        "industries": ["黄金", "银行"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 2, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "一年首次加息+鹰派点阵图，黄金承压、人民币逼近7关口，新兴市场流动性收紧",
    },
]


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def build() -> dict:
    final = []
    for e in EVENTS:
        dk = make_dedup_key(
            e["event_type"], e["announcement_date"], e["key_entities"], description=e["description"],
        )
        final.append({
            "dedup_key": dk,
            "description": e["description"],
            "event_type": e["event_type"],
            "shock_dimension": e["shock_dimension"],
            "impact_direction": e["impact_direction"],
            "announcement_date": e["announcement_date"],
            "key_entities": e["key_entities"],
            "industries": e["industries"],
            "significance": _clamp(e["significance"], 1, 4),
            "a_share_relevance": _clamp(e["a_share_relevance"], 1, 5),
            "surprise_timing": _clamp(e["surprise_timing"], 1, 5),
            "surprise_magnitude": _clamp(e["surprise_magnitude"], 1, 5),
            "impact_scale": _clamp(e["impact_scale"], 1, 5),
            "source": "llm_knowledge",
        })
    return {
        "final": final,
        "llm_rejected": [],
        "process_summary": {
            "raw_count": len(EVENTS),
            "filtered_out": 0,
            "llm_rejected": 0,
            "final": len(final),
            "method": "人工搜集 + web搜索核实日期，聚焦外部系统性冲击（军事/政治/经济/地缘）",
            "note": "非 mine-events 自动产物：由 Claude 基于知识+搜索整理，经 make_dedup_key 生成 dedup_key，格式与 mining_candidates 兼容",
        },
    }


if __name__ == "__main__":
    out = build()
    path = Path("data/mining_candidates_2016-01-01_2016-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']:3}/{c['impact_direction']:2} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:34]}")
