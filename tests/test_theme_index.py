"""ThemeIndex + CandidateGen 主题补充测试（治召回遗漏）。"""

from datetime import date

from eventreplay.adapters.stock_master import ResolvedStock
from eventreplay.adapters.theme_index import ThemeIndex
from eventreplay.pipeline.nodes.candidate import CandidateNode, _CandsOut
from eventreplay.schema import ChainNode, StockDirection


def test_theme_index_lookup_finds_zhongchuanteqi():
    ti = ThemeIndex("data/themes.yaml")
    hits = ti.lookup("中游·电子特气/半导体特种气体")
    codes = [h["code"] for h in hits]
    assert "688146" in codes  # 中船特气


def test_theme_index_lookup_finds_re_magnetic():
    ti = ThemeIndex("data/themes.yaml")
    hits = ti.lookup("稀土永磁电机")
    codes = [h["code"] for h in hits]
    assert "300748" in codes  # 金力永磁


class _FakeSM:
    def __init__(self, code_map):
        self.code_map = code_map

    def resolve(self, ident):
        return self.code_map.get(ident)


def test_candidate_node_supplements_theme_stock_llm_missed():
    """LLM 没提议中船特气，但产业链节点含'电子特气' → 主题补充应把它加进候选。"""
    sm = _FakeSM({"688146": ResolvedStock("688146", "中船特气", "沪", "科创板", date(2023, 4, 21))})
    node = ChainNode(
        id="n1",
        label="电子特气（半导体特种气体）",
        sw_industry_l1="电子",
        level="中游",
        shock_direction=StockDirection.BENEFICIARY,
    )
    ti = ThemeIndex("data/themes.yaml")

    class FakeReasoner:
        model_alias = "fake"

        def call(self, **kw):
            return _CandsOut(candidates=[])  # LLM 一个都没提议

    cn = CandidateNode(FakeReasoner(), sm, constituent_sources=[ti])
    out = cn.post_process(_CandsOut(candidates=[]), {"event": None, "chain_nodes": [node]})
    codes = [c["stock_id"] for c in out]
    assert "688146" in codes, "主题补充应把 LLM 漏掉的中船特气加进来"
    # 方向继承自节点
    zhong = [c for c in out if c["stock_id"] == "688146"][0]
    assert zhong["direction"] == StockDirection.BENEFICIARY
    assert "电子特气" in zhong["rationale"]


def test_candidate_node_no_direction_skips_theme():
    """节点无 shock_direction → 不补充（无法分类方向）。"""
    sm = _FakeSM({"688146": ResolvedStock("688146", "中船特气", "沪", "科创板", date(2023, 4, 21))})
    node = ChainNode(id="n1", label="电子特气", level="中游", shock_direction=None)
    ti = ThemeIndex("data/themes.yaml")

    class FakeReasoner:
        model_alias = "fake"
        def call(self, **kw):
            return _CandsOut(candidates=[])

    cn = CandidateNode(FakeReasoner(), sm, constituent_sources=[ti])
    out = cn.post_process(_CandsOut(candidates=[]), {"event": None, "chain_nodes": [node]})
    assert out == []
