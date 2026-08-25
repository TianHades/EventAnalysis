"""TushareSource 测试：无 token→空；有 token→经申万三级权威命中中船特气。

离线段（mock pro API，不触网）：_build_reverse_map 懒拉取 + L1 常用词黑名单。
"""

import os

import pandas as pd
import pytest
from dotenv import load_dotenv

from eventreplay.adapters.tushare_source import TushareSource

load_dotenv()


def test_tushare_no_token_no_cache_returns_empty(monkeypatch, tmp_path):
    """无 token + 无磁盘缓存 → 空（有缓存时即使无 token 也能读，这是持久缓存的意义）。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    src = TushareSource(cache_dir=tmp_path)  # 空缓存目录
    assert src.lookup("半导体材料") == []


# ---- 离线（mock pro，不触网）----


class _FakePro:
    """假 tushare pro：L3 半导体材料(含中船特气/江丰) + L1 电子兜底。"""

    def index_classify(self, level="L3", src="SW2021"):
        if level == "L3":
            return pd.DataFrame({
                "index_code": ["850813.SI"],
                "industry_name": ["半导体材料"],
            })
        return pd.DataFrame({"index_code": ["801080.SI"], "industry_name": ["电子"]})

    def index_member(self, index_code=""):
        return pd.DataFrame({
            "con_code": ["688146.SH", "300666.SZ"],
            "out_date": [None, None],
        })

    def stock_basic(self, fields=""):
        return pd.DataFrame({
            "ts_code": ["688146.SH", "300666.SZ"],
            "name": ["中船特气", "江丰电子"],
            "industry": ["电子", "电子"],
        })


def test_classify_lazy_fetches_members_not_degraded_to_l1(tmp_path, monkeypatch):
    """首机无磁盘 member 缓存 → _build_reverse_map 经 _members 在线拉取（不静默降级 L1）。"""
    monkeypatch.setattr(TushareSource, "_api", lambda self: _FakePro())
    src = TushareSource(cache_dir=tmp_path)  # 空缓存目录（无 sw_members.pkl）
    assert src.classify("688146") == "半导体材料"  # L3 命中，而非 L1 兜底 "电子"


def test_lookup_l1_blacklist_filters_generic_words(tmp_path, monkeypatch):
    """"综合"等常用词命中黑名单：不把申万一级"综合"成分股注入无关文本。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    src = TushareSource(cache_dir=tmp_path)
    monkeypatch.setattr(
        src, "industry_index_codes", lambda: {"综合": "801230.SI", "银行": "801780.SI"}
    )
    monkeypatch.setattr(
        src, "_members", lambda ic: {"600036"} if ic == "801780.SI" else {"601398"}
    )
    hits = src.lookup("综合分析：银行板块走弱")
    themes = {h["theme"] for h in hits}
    assert "申万L1:银行" in themes
    assert "申万L1:综合" not in themes  # "综合"在黑名单
    # 文本只含黑名单词 → 零注入
    assert src.lookup("总体综合评估") == []


@pytest.mark.skipif(not os.getenv("TUSHARE_TOKEN"), reason="无 TUSHARE_TOKEN")
def test_tushare_lookup_finds_semiconductor_material_members():
    """节点文本含'半导体材料' → 申万三级 850813 成分股，应含中船特气/江丰。"""
    src = TushareSource()
    hits = src.lookup("中游·半导体材料（靶材/CMP/衬底）")
    codes = [h["code"] for h in hits]
    assert "688146" in codes, "应含中船特气（申万半导体材料成分股）"
    assert "300666" in codes, "应含江丰电子"
    # theme 标注含申万来源
    assert any("申万" in h["theme"] for h in hits)


@pytest.mark.skipif(not os.getenv("TUSHARE_TOKEN"), reason="无 TUSHARE_TOKEN")
def test_tushare_lookup_no_match_returns_empty():
    src = TushareSource()
    # "K12教培" 不匹配任何申万三级名
    assert src.lookup("K12校外学科类培训") == []
