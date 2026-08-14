"""HistoricalEventMiner 测试：多阶段（LLM列候选 → 硬过滤 → LLM评估选取）+ 中间数据落盘。"""

import json

from eventreplay.pipeline.nodes.historical_miner import (
    HistoricalEventMiner,
    _EventMineOut,
    _EventSelectOut,
    _MinedEventOut,
    _SelectItemOut,
)


class FakeReasoner:
    """按 output_model 分发：_EventMineOut（列候选）/ _EventSelectOut（评估选取）。"""

    model_alias = "fake"

    def __init__(self, mine_out: _EventMineOut, select_out: _EventSelectOut):
        self.mine_out = mine_out
        self.select_out = select_out

    def call(self, *, system, user, output_model):
        if output_model is _EventMineOut:
            return self.mine_out
        if output_model is _EventSelectOut:
            return self.select_out
        raise AssertionError(f"未mock: {output_model}")


def _mined(desc: str, sig: int = 3, a: int = 4, sd: str = "政策") -> _MinedEventOut:
    return _MinedEventOut(
        description=desc, event_type="政策", shock_dimension=sd, impact_direction="利好",
        announcement_date="2023-03-17", key_entities=["央行"], industries=["银行"],
        significance=sig, a_share_relevance=a, rationale="重要事件",
    )


def test_mine_three_stages_with_intermediate_files(tmp_path):
    """完整三阶段：列候选 → 硬过滤 → 评估选取，中间 json 落盘。"""
    # 阶段1：4 候选（B sig=1 噪音，C a=2 不可达，A/D 合格）
    mine_out = _EventMineOut(events=[
        _mined("事件A", sig=4, a=5),
        _mined("事件B", sig=1, a=4),  # 硬过滤剔除（sig<2）
        _mined("事件C", sig=3, a=2),  # 硬过滤剔除（a<3）
        _mined("事件D", sig=3, a=4),
    ])
    # 阶段2：kept=[A, D]（index 0, 1），选两个
    select_out = _EventSelectOut(
        selected=[_SelectItemOut(index=0, reason="系统级，全市场影响"),
                  _SelectItemOut(index=1, reason="主线级，多板块传导")],
        rejected=[],
    )
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    # 三阶段计数
    assert result["raw_count"] == 4
    assert result["filtered_out"] == 2  # B + C
    assert len(result["final"]) == 2  # A + D
    # 中间文件落盘
    assert (tmp_path / "mining_raw_2023-01-01_2023-12-31.json").exists()
    assert (tmp_path / "mining_filtered_2023-01-01_2023-12-31.json").exists()
    assert (tmp_path / "mining_candidates_2023-01-01_2023-12-31.json").exists()
    # final json 含被剔除 + summary
    data = json.loads((tmp_path / "mining_candidates_2023-01-01_2023-12-31.json").read_text(encoding="utf-8"))
    assert "final" in data
    assert "llm_rejected" in data
    assert data["process_summary"] == {"raw_count": 4, "filtered_out": 2, "llm_rejected": 0, "final": 2}
    # final 候选含 dedup_key + clamp 后的 sig
    assert data["final"][0]["description"] == "事件A"
    assert data["final"][0]["significance"] == 4
    assert data["final"][0]["dedup_key"]


def test_hard_filter_drops_low_sig_and_low_a_share(tmp_path):
    """sig<2 或 a_share<3 被硬过滤（附原因）。"""
    mine_out = _EventMineOut(events=[
        _mined("噪音", sig=1, a=4),
        _mined("不可达", sig=3, a=2),
        _mined("合格", sig=3, a=4),
    ])
    select_out = _EventSelectOut(selected=[_SelectItemOut(index=0, reason="选")], rejected=[])
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert result["filtered_out"] == 2
    # 过滤文件含被剔除项 + 原因
    data = json.loads((tmp_path / "mining_filtered_2023-01-01_2023-12-31.json").read_text(encoding="utf-8"))
    reasons = {d["description"]: d["原因"] for d in data["dropped"]}
    assert "噪音" in reasons and "sig=1" in reasons["噪音"]
    assert "不可达" in reasons and "a_share=2" in reasons["不可达"]


def test_llm_select_uses_kept_indices(tmp_path):
    """阶段2 LLM 用 kept（硬过滤后）的 index 选取。"""
    mine_out = _EventMineOut(events=[
        _mined("A", sig=4),
        _mined("B", sig=1),  # 硬过滤
        _mined("C", sig=3),
    ])
    # kept = [A, C]（index 0, 1），只选 A
    select_out = _EventSelectOut(
        selected=[_SelectItemOut(index=0, reason="最重要")],
        rejected=[_SelectItemOut(index=1, reason="与A同类次之")],
    )
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert len(result["final"]) == 1
    assert result["final"][0].event.description == "A"
    assert result["llm_rejected"] == 1


def test_empty_kept_skips_llm_select(tmp_path):
    """阶段1.5 全过滤 → 阶段2 不调 LLM（空 final）。"""
    mine_out = _EventMineOut(events=[_mined("噪音", sig=1, a=1)])

    class _NoSelectReasoner:
        model_alias = "fake"

        def call(self, *, system, user, output_model):
            if output_model is _EventMineOut:
                return mine_out
            raise AssertionError("kept 为空不应调阶段2 LLM")

    miner = HistoricalEventMiner(_NoSelectReasoner(), None)
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert result["final"] == []
    assert result["filtered_out"] == 1
    assert result["llm_rejected"] == 0


def test_significance_clamped(tmp_path):
    """LLM 打分越界 → clamp 到 1-4/1-5。"""
    mine_out = _EventMineOut(events=[_mined("越界", sig=9, a=8)])
    select_out = _EventSelectOut(selected=[_SelectItemOut(index=0, reason="选")], rejected=[])
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert result["final"][0].significance == 4  # 9→4
    assert result["final"][0].a_share_relevance == 5  # 8→5
