"""本轮代码审查修复的回归测试（覆盖此前无测试的盲区）。

对应修复：
- max_drawdown 仅在全局最低点取回撤 → 改为全路径峰值回撤
- 行业基准 CAR 窗口不足时截断冒充完整窗口 → 改为 None
- jieba 为可选依赖：缺失时 _semantic_ok 退化而非崩溃
- 申万一级行业名白名单：规范映射不再被词面护栏误伤
- ForwardMapping 缓存命中后重建为模型对象（post 校准可用）
- search_patterns 跨维度向量不崩溃（降级 score=0）
- 北交所 920 号段 → bj 前缀
- 近期事件上下文保留最新的 8 条
- LLM 名称带括号附注可反查
"""

from __future__ import annotations

import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

from eventreplay.adapters.quote_adapter import to_sina_symbol
from eventreplay.adapters.stock_master import clean_name
from eventreplay.measurement.event_study import measure_stock
from eventreplay.pipeline.nodes.chain import _semantic_ok
from eventreplay.pipeline.nodes.forward_mapping import ForwardMappingNode, ForwardMappingResult
from eventreplay.schema import Board, StockDirection
from eventreplay.schema.event import Event
from eventreplay.storage import Storage


# ---- E1: max_drawdown 全路径峰值回撤 ----


def _v_shape_then_drop_quotes() -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """先探底、再创新高、然后深跌：旧实现只在全局最低点取回撤，会漏掉后续更深回撤。"""
    n, t0 = 400, 300
    rng = np.random.default_rng(7)
    bench_ret = rng.normal(0, 0.001, n)
    abnormal = np.zeros(n)
    abnormal[t0 - 5 : t0] = -0.002          # 探底（全局最低点在此附近）
    abnormal[t0 : t0 + 30] = 0.005          # V 型修复创新高
    abnormal[t0 + 30 : t0 + 60] = -0.006    # 高位深跌（真实最大回撤）
    stock_ret = bench_ret + abnormal
    dates = pd.bdate_range("2022-01-03", periods=n).date

    def to_ohlcv(rets):
        close = 100 * np.cumprod(1 + rets)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
                "turnover": 0.02,
            }
        )

    stock = to_ohlcv(stock_ret)
    bench = pd.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + bench_ret)})
    return stock, bench, t0


def test_max_drawdown_captures_post_peak_fall():
    stock, bench, t0 = _v_shape_then_drop_quotes()
    m = measure_stock(
        stock_id="TEST",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
        direction=StockDirection.BENEFICIARY,
    )
    assert m is not None
    # 事件窗 [t0, t0+90] 覆盖创新高（~+15%）与随后的深跌（~-18%）：
    # 峰值回撤应明显大于早期探底幅度（旧实现约 -1%量级，会严重低估）
    assert m.max_drawdown is not None
    assert m.max_drawdown <= -0.10


# ---- E4: 行业 CAR 窗口不足 → None（不截断） ----


def test_industry_car_none_when_window_insufficient():
    stock, bench, t0 = _v_shape_then_drop_quotes()
    # 行业指数序列截短：只覆盖 t0+40 → [0,+60]/[0,+90] 窗口必然不足
    ind_dates = stock["date"].iloc[: t0 + 41]
    ind = pd.DataFrame({"date": ind_dates, "close": 100 * np.cumprod(1 + np.zeros(len(ind_dates)))})
    m = measure_stock(
        stock_id="TEST",
        episode_id="e0",
        quotes=stock,
        bench=bench,
        reaction_date=stock["date"].iloc[t0],
        board=Board.MAIN,
        is_st=False,
        listing_date=stock["date"].iloc[0],
        direction=StockDirection.BENEFICIARY,
        bench_industry=ind,
    )
    assert m is not None
    assert m.car_industry_0_20 is not None      # +20 窗口充足
    assert m.car_industry_0_60 is None          # 截断的旧实现会给出部分窗口值
    assert m.car_industry_0_90 is None


# ---- E2: jieba 缺失时 _semantic_ok 退化 ----


def test_semantic_ok_without_jieba(monkeypatch):
    monkeypatch.setitem(sys.modules, "jieba", None)  # import jieba → ImportError
    assert _semantic_ok("K12教培", "教培行业") is True          # 字符重叠
    assert _semantic_ok("高端装备制造", "消费电子") is False     # 无重叠且非申万一级名 → 低置信


def test_semantic_ok_sw_l1_whitelist():
    """规范申万一级行业名直接放行（词面无关但映射正确）。"""
    assert _semantic_ok("光伏组件", "电力设备") is True
    assert _semantic_ok("锂电材料", "电力设备") is True
    assert _semantic_ok("某未知环节", "不存在的行业名") is False


# ---- E3: ForwardMapping 缓存命中重建为模型对象 ----


class _FakeReasoner:
    model_alias = "fake"

    def call(self, *, system, user, output_model):
        from eventreplay.pipeline.nodes.forward_mapping import _BenefOut, _PredictedStockOut, _VictOut

        if output_model is _BenefOut:
            return _BenefOut(
                transmission_logic="受益逻辑",
                beneficiaries=[_PredictedStockOut(code="600519", name="贵州茅台", direction="受益", rationale="r")],
                confidence="高",
                key_risks="风险",
            )
        if output_model is _VictOut:
            return _VictOut(transmission_logic="受损逻辑", victims=[])


class _FakeSM:
    def resolve(self, identifier):
        from eventreplay.adapters.stock_master import ResolvedStock

        if identifier in ("600519", "贵州茅台"):
            return ResolvedStock(code="600519", name="贵州茅台", market="沪", board="主板", listing_date=date(2001, 8, 27))
        return None


def _ev() -> Event:
    return Event(
        id="e1",
        description="测试事件",
        announcement_date=date(2026, 1, 5),
        reaction_date=date(2026, 1, 5),
        event_type="测试",
    )


def test_forward_mapping_cache_rebuild_returns_model():
    storage = Storage(":memory:")
    node = ForwardMappingNode(_FakeReasoner(), _FakeSM(), storage=storage)
    inputs = {"event": _ev(), "patterns": [], "scores": [], "degradation_level": "strong", "_replay_id": "r1"}

    out1 = node.run_cached(inputs)
    assert isinstance(out1, ForwardMappingResult)
    assert len(out1.beneficiary_predictions) == 1

    out2 = node.run_cached(inputs)  # 第二次：SQLite 缓存命中路径
    assert isinstance(out2, ForwardMappingResult)
    # post 模式校准依赖 model_copy：缓存命中后对象必须可用
    pred = out2.beneficiary_predictions[0]
    assert pred.model_copy(update={"calibration": "neutral"}).calibration == "neutral"


# ---- E5: 跨维度向量检索不崩溃 ----


def test_search_patterns_dim_mismatch_degrades():
    import json

    st = Storage(":memory:")
    payload = json.dumps(
        {
            "id": "pat-p1", "source_replay_id": "p1", "created_at": "2026-01-01T00:00:00",
            "model_alias": "m", "prompt_hash": "h", "shock_dimension": "政策",
            "impact_direction": "利好", "event_type": "t", "event_summary": "s",
            "transmission_template": "tt",
        },
        ensure_ascii=False,
    )
    st._conn.execute(
        "INSERT INTO patterns(source_replay_id, payload, embedding, embedding_model, created_at) VALUES ('p1', ?, ?, 'bge', ?)",
        (payload, np.arange(512, dtype=np.float32).tobytes(), "2026-01-01"),
    )
    st._conn.commit()
    q = list(np.ones(2048, dtype=np.float32))  # zhipu 维度查询 vs 512 维库存
    results = st.search_patterns(q, top_k=3)   # 旧实现 ValueError
    assert len(results) == 1
    assert results[0][1] == 0.0                # 维度不符 → 跳过向量打分


# ---- E9c: 北交所 920 号段 ----


def test_to_sina_symbol_bse_920():
    assert to_sina_symbol("920001") == "bj920001"
    assert to_sina_symbol("830799") == "bj830799"
    assert to_sina_symbol("688981") == "sh688981"
    assert to_sina_symbol("900901") == "sh900901"  # 沪 B 仍走 sh


# ---- E9b: 近期上下文保留最新条目 ----


def test_recent_context_keeps_newest():
    from eventreplay.pipeline.event_dedup import EventDedup
    from eventreplay.pipeline.monitoring_pusher import MonitoringPusher

    st = Storage(":memory:")
    # 显式递增 seen_at（Windows 时钟粒度 ~15ms，同批 utcnow 可能相同导致排序不稳定）
    for i in range(10):
        st._conn.execute(
            "INSERT INTO seen_events(dedup_key, source, payload, seen_at, purpose) VALUES (?,?,?,?, 'push')",
            (f"key{i}", "news", f'{{"description": "事件{i}"}}', f"2026-08-{i + 1:02d}T00:00:00"),
        )
    st._conn.commit()
    pusher = MonitoringPusher.__new__(MonitoringPusher)
    pusher.dedup = EventDedup(st)
    ctx = pusher._build_recent_context(echo=lambda *_: None)
    assert "事件9" in ctx      # 最新一条必须在（旧实现 lines[-8:] 会把最新 2 条切掉）
    assert "事件2" in ctx
    assert "事件1" not in ctx  # 只保留最新 8 条
    assert "事件0" not in ctx


# ---- I4: 名称去括号附注 ----


def test_clean_name_strips_parenthetical():
    assert clean_name("宁德时代（300750）") == "宁德时代"
    assert clean_name("宁德时代(300750)") == "宁德时代"
    assert clean_name("  贵州茅台 ") == "贵州茅台"
    assert clean_name("京东方A") == "京东方A"
