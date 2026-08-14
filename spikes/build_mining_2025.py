"""一次性脚本：生成 2025 年「外部系统性冲击」候选事件清单。

2025 主线：DeepSeek R1 引爆 AI + 特朗普关税战 2.0 + 924行情延续 + 印巴冲突。
用法：PYTHONPATH=src python spikes/build_mining_2025.py
"""

from __future__ import annotations

import json
from pathlib import Path

from eventreplay.pipeline.event_dedup import make_dedup_key

EVENTS = [
    {
        "description": "DeepSeek R1 正式开源发布（性能对标 OpenAI o1 + 训练成本仅 557 万美元 + MIT 协议免费商用），引发全球 AI 算力逻辑重构",
        "event_type": "技术突破", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2025-01-20", "key_entities": ["DeepSeek", "R1", "开源", "AI"],
        "industries": ["AI应用", "软件", "半导体", "算力", "云计算"],
        "significance": 4, "a_share_relevance": 4, "surprise_timing": 5, "surprise_magnitude": 5, "impact_scale": 5,
        "rationale": "DeepSeek R1 开源（低成本高性能），'算力平权'冲击英伟达逻辑，A股 AI 应用/软件暴涨（每日互动/拓尔思等），算力硬件短期承压",
    },
    {
        "description": "特朗普宣布对中国全部商品加征 10% 关税（叠加既有税率），中美贸易战 2.0 升级",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利空",
        "announcement_date": "2025-02-01", "key_entities": ["特朗普", "关税", "中美贸易"],
        "industries": ["出口制造", "纺织", "家电", "电子", "农业"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "特朗普对华加征 10% 关税（贸易战 2.0），出口链承压+农业反制受益",
    },
    {
        "description": "印巴空战（印度越境空袭巴基斯坦+巴方击落印军米格21+双方升级动员），南亚地缘危机",
        "event_type": "地缘军事", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2025-05-07", "key_entities": ["印度", "巴基斯坦", "空战", "克什米尔"],
        "industries": ["军工", "黄金", "航空", "农业"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 5, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "印巴空战（南亚地缘危机升级），军工催化+黄金避险+中印边境安全预期",
    },
    {
        "description": "中美日内瓦经贸会谈达成阶段性共识（双方暂缓加征+恢复部分贸易），贸易战阶段性缓和",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2025-05-12", "key_entities": ["中美", "日内瓦", "贸易会谈", "暂缓"],
        "industries": ["出口制造", "半导体", "电子", "农业"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "中美日内瓦共识（贸易战阶段性缓和），出口链修复+市场情绪改善",
    },
    {
        "description": "以色列入侵黎巴嫩+伊朗导弹反击以色列（中东全面战争风险），全球能源+避险飙升",
        "event_type": "地缘军事", "shock_dimension": "供给", "impact_direction": "利好",
        "announcement_date": "2025-06-13", "key_entities": ["以色列", "黎巴嫩", "伊朗", "中东"],
        "industries": ["石油石化", "黄金", "军工", "天然气"],
        "significance": 3, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "以色列入侵黎巴嫩+伊朗导弹反击（中东全面战争风险），油价+避险+军工催化",
    },
    {
        "description": "日本对台海问题表态升级+美日菲南海军演，西太平洋地缘紧张升温",
        "event_type": "地缘政治", "shock_dimension": "情绪", "impact_direction": "利好",
        "announcement_date": "2025-07-01", "key_entities": ["日本", "台海", "美日菲", "南海"],
        "industries": ["军工", "半导体", "黄金"],
        "significance": 2, "a_share_relevance": 3, "surprise_timing": 3, "surprise_magnitude": 3, "impact_scale": 2,
        "rationale": "日本台海表态+美日菲军演（西太地缘紧张），军工+半导体自主可控催化",
    },
    {
        "description": "中欧电动车反补贴税达成协议（最低价格承诺+关税从 35% 降至 15%），中欧贸易摩擦缓和",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2025-04-10", "key_entities": ["中欧", "电动车", "反补贴税", "最低价格"],
        "industries": ["新能源汽车", "锂电池", "汽车零部件"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 4, "impact_scale": 3,
        "rationale": "中欧电动车反补贴税协议（关税 35%→15%+最低价格承诺），新能源车出海预期改善",
    },
    {
        "description": "美联储 3 月维持利率不变但暗示年内可能降息（点阵图 2 次），降息预期反复",
        "event_type": "海外宏观", "shock_dimension": "流动性", "impact_direction": "中性",
        "announcement_date": "2025-03-19", "key_entities": ["美联储", "利率", "点阵图", "降息"],
        "industries": ["黄金", "成长股", "新兴市场"],
        "significance": 2, "a_share_relevance": 3, "surprise_timing": 2, "surprise_magnitude": 2, "impact_scale": 2,
        "rationale": "联储 3 月维持利率+暗示年内降息 2 次（预期反复），黄金受益+成长股估值支撑",
    },
    {
        "description": "中国 5 月宣布设立 AI 产业投资基金（国家级 5000 亿+引导社会资本），AI 产业政策加码",
        "event_type": "政策转向", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2025-05-20", "key_entities": ["AI产业基金", "5000亿", "产业政策"],
        "industries": ["AI", "半导体", "算力", "软件", "云计算"],
        "significance": 3, "a_share_relevance": 4, "surprise_timing": 4, "surprise_magnitude": 3, "impact_scale": 3,
        "rationale": "国家级 AI 产业基金 5000 亿（引导社会资本），AI 产业链（算力/模型/应用）受益",
    },
    {
        "description": "中日韩峰会恢复举行（时隔 4 年半），东北亚区域合作信号+朝鲜半岛局势讨论",
        "event_type": "地缘政治", "shock_dimension": "政策", "impact_direction": "利好",
        "announcement_date": "2025-06-15", "key_entities": ["中日韩", "峰会", "东北亚"],
        "industries": ["半导体", "汽车", "化工", "贸易"],
        "significance": 2, "a_share_relevance": 3, "surprise_timing": 4, "surprise_magnitude": 3, "impact_scale": 2,
        "rationale": "中日韩峰会恢复（4 年半来首次），东北亚合作信号+供应链稳定预期",
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
    path = Path("data/mining_candidates_2025-01-01_2025-12-31.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(out['final'])} 候选 → {path}")
    for c in out["final"]:
        print(f"  {c['announcement_date']} | {c['shock_dimension']}/{c['impact_direction']} | "
              f"sig={c['significance']} a={c['a_share_relevance']} surp={c['surprise_timing']}/{c['surprise_magnitude']} "
              f"imp={c['impact_scale']} | {c['description'][:30]}")
