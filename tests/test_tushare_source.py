"""TushareSource 测试：无 token→空；有 token→经申万三级权威命中中船特气。"""

import os

import pytest
from dotenv import load_dotenv

from eventreplay.adapters.tushare_source import TushareSource

load_dotenv()


def test_tushare_no_token_no_cache_returns_empty(monkeypatch, tmp_path):
    """无 token + 无磁盘缓存 → 空（有缓存时即使无 token 也能读，这是持久缓存的意义）。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    src = TushareSource(cache_dir=tmp_path)  # 空缓存目录
    assert src.lookup("半导体材料") == []


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
