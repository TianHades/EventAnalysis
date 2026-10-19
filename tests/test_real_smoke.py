"""M5 真实端到端冒烟（双减事件）：真实 claude + 真实 sina 数据 + 真实 StockMaster。

成本：~6 次 claude -p 调用 + StockMaster 加载(~46s)。手动/CI 慢速运行。
环境不可达（claude/sina）则 skip。
"""

import os
from datetime import date

import pytest

from eventreplay.adapters.quote_adapter import AkshareQuoteAdapter
from eventreplay.adapters.stock_master import StockMaster
from eventreplay.llm import ClaudeCodeProvider, LLMNodeFailed
from eventreplay.pipeline.orchestrator import ReplayOrchestrator
from eventreplay.storage import Storage

# pytest 不会自动加载 .env；本测试需网关鉴权，自行加载以保证 `pytest tests/test_real_smoke.py` 可直接跑
# （只影响本文件，不改变其他测试的行为）
from dotenv import load_dotenv

load_dotenv()


def _env_ready() -> bool:
    return bool(os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(not _env_ready(), reason="无 ANTHROPIC 鉴权环境变量")
def test_double_reduction_real_end_to_end(tmp_path):
    # 真实组件
    reasoner = ClaudeCodeProvider(model_alias="sonnet", timeout_s=300)
    qa = AkshareQuoteAdapter(cache_dir=tmp_path / "q")
    try:
        sm = StockMaster(cache_dir=tmp_path / "sm")
        sm.ensure_loaded()
        if not sm._master:
            pytest.skip("StockMaster 加载为空（网络）")
    except ConnectionError as e:
        pytest.skip(f"StockMaster 网络不可达: {e}")

    storage = Storage(tmp_path / "t.db")
    orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, reports_dir=tmp_path / "reports")

    try:
        markdown, path = orch.replay(
            description="2021年7月24日中共中央办公厅、国务院办公厅印发《关于进一步减轻义务教育阶段学生作业负担和校外培训负担的意见》（双减），严厉规范 K12 校外培训。",
            announcement_date="2021-07-24",
            reaction_date="2021-07-26",
            event_id="double_reduction_real",
            benchmark="sh000300",
        )
    except LLMNodeFailed as e:
        pytest.skip(f"claude 推理失败: {str(e)[:120]}")

    # 断言：报告生成、写入磁盘、受益或受损池至少有一个非空、度量非空
    assert path.exists()
    assert len(markdown) > 200
    chain = storage.get_chain("double_reduction_real")
    assert chain is not None
    ms = storage.get_measurements("double_reduction_real")
    assert len(ms) >= 1, "至少应度量 1 只候选标的"
    has_pool = bool(chain.beneficiary_stock_ids or chain.victim_stock_ids)
    assert has_pool, "受益或受损池至少一个应非空"
