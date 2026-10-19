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


def _mined(
    desc: str,
    sig: int = 3,
    a: int = 4,
    sd: str = "政策",
    announcement_date: str = "2023-03-17",
    announcement_time: str | None = None,
) -> _MinedEventOut:
    return _MinedEventOut(
        description=desc, event_type="政策", shock_dimension=sd, impact_direction="利好",
        announcement_date=announcement_date, announcement_time=announcement_time,
        key_entities=["央行"], industries=["银行"],
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
    # 阶段2：kept=[A, D]（1-based 行号 1, 2），选两个
    select_out = _EventSelectOut(
        selected=[_SelectItemOut(index=1, reason="系统级，全市场影响"),
                  _SelectItemOut(index=2, reason="主线级，多板块传导")],
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
    select_out = _EventSelectOut(selected=[_SelectItemOut(index=1, reason="选")], rejected=[])
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
    # kept = [A, C]（1-based 行号 1, 2），只选 A
    select_out = _EventSelectOut(
        selected=[_SelectItemOut(index=1, reason="最重要")],
        rejected=[_SelectItemOut(index=2, reason="与A同类次之")],
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
    select_out = _EventSelectOut(selected=[_SelectItemOut(index=1, reason="选")], rejected=[])
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert result["final"][0].significance == 4  # 9→4
    assert result["final"][0].a_share_relevance == 5  # 8→5


# ---- 修复：盘后 → 次交易日 / 日期兜底 / 日期越界 / 1-based 选取 / 去重截断 / market 动态 ----


def test_mined_after_close_reaction_next_day(tmp_path):
    """盘后公告的候选 → reaction_date = announcement_date + 1 天。"""
    mine_out = _EventMineOut(events=[_mined("盘后事件", announcement_time="盘后")])
    select_out = _EventSelectOut(selected=[_SelectItemOut(index=1, reason="选")], rejected=[])
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    ev = result["final"][0].event
    from datetime import date

    assert ev.announcement_date == date(2023, 3, 17)
    assert ev.reaction_date == date(2023, 3, 18)
    assert ev.announcement_time.value == "盘后"


def test_hard_filter_drops_unparseable_date(tmp_path):
    """announcement_date 不可解析 → 丢弃计入 dropped（理由"日期不可解析"），不虚构 today()。"""
    mine_out = _EventMineOut(events=[_mined("坏日期", announcement_date="不是日期")])
    select_out = _EventSelectOut(selected=[], rejected=[])
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert result["final"] == []
    data = json.loads((tmp_path / "mining_filtered_2023-01-01_2023-12-31.json").read_text(encoding="utf-8"))
    reasons = {d["description"]: d["原因"] for d in data["dropped"]}
    assert "坏日期" in reasons and "日期不可解析" in reasons["坏日期"]


def test_hard_filter_drops_out_of_range_date(tmp_path):
    """announcement_date 超出挖掘日期范围 → 丢弃（理由"日期越界"）。"""
    mine_out = _EventMineOut(events=[
        _mined("太早", announcement_date="2022-06-01"),
        _mined("太晚", announcement_date="2024-06-01"),
        _mined("边界内", announcement_date="2023-03-17"),
    ])
    select_out = _EventSelectOut(selected=[_SelectItemOut(index=1, reason="选")], rejected=[])
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert len(result["final"]) == 1
    assert result["final"][0].event.description == "边界内"
    data = json.loads((tmp_path / "mining_filtered_2023-01-01_2023-12-31.json").read_text(encoding="utf-8"))
    reasons = {d["description"]: d["原因"] for d in data["dropped"]}
    assert "日期越界" in reasons["太早"] and "日期越界" in reasons["太晚"]


def test_llm_select_dedupes_and_truncates_to_top_n(tmp_path):
    """selected 重复 index → 去重；超出 top_n → 截断。"""
    mine_out = _EventMineOut(events=[_mined("A"), _mined("B"), _mined("C")])
    select_out = _EventSelectOut(
        selected=[
            _SelectItemOut(index=1, reason="选A"),
            _SelectItemOut(index=1, reason="又选A（重复）"),
            _SelectItemOut(index=2, reason="选B"),
            _SelectItemOut(index=3, reason="选C（超出top_n=2）"),
        ],
        rejected=[],
    )
    miner = HistoricalEventMiner(FakeReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=2,
        out_dir=tmp_path, echo=lambda x: None,
    )
    descs = [c.event.description for c in result["final"]]
    assert descs == ["A", "B"]  # 去重 + 截断，保持 LLM 顺序


def test_llm_select_lines_are_one_based(tmp_path):
    """候选行号 1-based 显示（[1] 起），LLM 回 index 按 1-based 解析。"""
    mine_out = _EventMineOut(events=[_mined("A"), _mined("B")])
    seen_user = {}

    class _CaptureReasoner(FakeReasoner):
        def call(self, *, system, user, output_model):
            if output_model is _EventSelectOut:
                seen_user["user"] = user
            return super().call(system=system, user=user, output_model=output_model)

    select_out = _EventSelectOut(selected=[_SelectItemOut(index=2, reason="选B")], rejected=[])
    miner = HistoricalEventMiner(_CaptureReasoner(mine_out, select_out))
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31", top_n=5,
        out_dir=tmp_path, echo=lambda x: None,
    )
    assert "[1]" in seen_user["user"]
    assert "[0]" not in seen_user["user"]
    assert result["final"][0].event.description == "B"  # index=2 → 第 2 个候选


def test_mine_system_prompt_uses_market():
    """_MINE_SYSTEM 按 market 参数动态生成（港股挖掘评分口径一致）。"""
    from eventreplay.pipeline.nodes.historical_miner import _mine_system

    a_share = _mine_system("A股")
    hk = _mine_system("港股")
    assert "A 股事件研究分析师" in a_share or "A股事件研究分析师" in a_share
    assert "港股" in hk
    assert "A 股" not in hk.replace("A股", "")  # 港股版本不再硬编码 A 股口径


def test_mine_stage1_llm_failure_returns_empty_paths(tmp_path):
    """阶段1 LLM 异常 → 降级返回 paths={} 且不落盘（CLI 据此友好报错而非 KeyError）。"""

    class _FailingReasoner:
        model_alias = "fake"

        def call(self, *, system, user, output_model):
            raise RuntimeError("网关超时")

    miner = HistoricalEventMiner(_FailingReasoner(), None)
    lines: list[str] = []
    result = miner.mine(
        start_date="2023-01-01", end_date="2023-12-31",
        out_dir=tmp_path, echo=lines.append,
    )
    assert result == {
        "final": [], "raw_count": 0, "filtered_out": 0, "llm_rejected": 0, "paths": {},
    }
    assert any("阶段1 LLM 失败" in line for line in lines)
    assert not list(tmp_path.glob("*.json"))  # 失败分支不落盘任何中间文件
