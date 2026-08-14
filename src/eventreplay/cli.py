"""命令行入口：eventreplay replay / extract-pattern / find-similar / forward。

启动时自动加载根目录 .env（凭证 + 默认值）；--model/--benchmark/--db 缺省读环境变量。
"""

from __future__ import annotations

import os

import typer
from dotenv import load_dotenv

# 模块导入时加载 .env（override=True：.env 优先于系统环境变量，便于切换网关/token）
load_dotenv(override=True)

from .adapters.quote_adapter import AkshareQuoteAdapter  # noqa: E402
from .adapters.stock_master import StockMaster  # noqa: E402
from .llm import ClaudeCodeProvider, Embedder  # noqa: E402
from .pipeline.forward_orchestrator import ForwardOrchestrator  # noqa: E402
from .pipeline.nodes.historical_miner import HistoricalEventMiner  # noqa: E402
from .pipeline.orchestrator import ReplayOrchestrator  # noqa: E402
from .pipeline.pattern_service import PatternService  # noqa: E402
from .schema import ImpactDirection, ShockDimension  # noqa: E402
from .storage import Storage  # noqa: E402

app = typer.Typer(add_completion=False, help="重大事件复盘分析系统")

_DEFAULT_MODEL = os.getenv("EVENTREPLAY_MODEL", "sonnet")
_DEFAULT_BENCHMARK = os.getenv("EVENTREPLAY_BENCHMARK", "sh000300")
_DEFAULT_DB = os.getenv("EVENTREPLAY_DB", "data/eventreplay.db")
_DEFAULT_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "240"))
_DEFAULT_FEISHU = os.getenv("FEISHU_WEBHOOK", "") or None


@app.command()
def replay(
    desc: str = typer.Option(..., "--desc", help="事件描述"),
    date: str = typer.Option(..., "--date", help="事件公告日 YYYY-MM-DD"),
    reaction_date: str | None = typer.Option(None, "--reaction-date", help="市场首反应日"),
    benchmark: str = typer.Option(_DEFAULT_BENCHMARK, "--benchmark", help="基准指数（A股 sh000300 / 港股 HSI）"),
    event_id: str = typer.Option("evt", "--event-id", help="事件 id（报告文件名）"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model", help="Claude 模型别名"),
    market: str = typer.Option("A股", "--market", help="A股/港股"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="SQLite 路径"),
) -> None:
    """Phase 1：对单个历史事件跑完整复盘流水线。"""
    typer.echo(f"[replay] {desc[:40]}... date={date} market={market} model={model}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    if market == "港股":
        from .adapters.hk_quote_adapter import HKQuoteAdapter
        from .adapters.hk_stock_master import HKStockMaster
        qa = HKQuoteAdapter()
        sm = HKStockMaster()
        benchmark = benchmark if benchmark != _DEFAULT_BENCHMARK else "HSI"
    else:
        qa = AkshareQuoteAdapter()
        sm = StockMaster()
    storage = Storage(db)
    orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, market=market)
    _, path = orch.replay(
        description=desc,
        announcement_date=date,
        reaction_date=reaction_date,
        event_id=event_id,
        benchmark=benchmark,
    )
    typer.echo(f"[done] 报告：{path}")


@app.command("extract-pattern")
def extract_pattern(
    replay_id: str = typer.Option(..., "--replay-id", help="已存 Replay 的 id"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model"),
    db: str = typer.Option(_DEFAULT_DB, "--db"),
) -> None:
    """Phase 2：从已存 Replay 提炼 Pattern 并存入规律库。"""
    reasoner = ClaudeCodeProvider(model_alias=model)
    embedder = Embedder()
    storage = Storage(db)
    svc = PatternService(storage, reasoner, embedder)
    p = svc.extract_pattern(replay_id)
    if p is None:
        typer.echo(f"[fail] 未找到 Replay {replay_id}（先 eventreplay replay 产生 Replay）")
        raise typer.Exit(1)
    typer.echo(
        f"[done] Pattern {p.id} | {p.shock_dimension.value}/{p.impact_direction.value} | "
        f"beneficiary={len(p.archetypal_beneficiaries)} victim={len(p.archetypal_victims)} | "
        f"emb={'y' if p.embedding else 'n'}"
    )


@app.command("find-similar")
def find_similar(
    desc: str = typer.Option(..., "--desc", help="事件描述"),
    shock_dimension: str | None = typer.Option(None, "--shock-dimension", help="供给/需求/政策/流动性/情绪"),
    direction: str | None = typer.Option(None, "--direction", help="利好/利空/中性/混合"),
    industry: str | None = typer.Option(None, "--industry", help="行业关键词"),
    top_k: int = typer.Option(3, "--top-k"),
    db: str = typer.Option(_DEFAULT_DB, "--db"),
) -> None:
    """Phase 2：检索相似历史 Pattern。"""
    embedder = Embedder()
    storage = Storage(db)
    svc = PatternService(storage, reasoner=None, embedder=embedder)  # type: ignore[arg-type]
    sd = ShockDimension(shock_dimension) if shock_dimension else None
    idir = ImpactDirection(direction) if direction else None
    results = svc.find_similar(desc, shock_dimension=sd, impact_direction=idir, industry=industry, top_k=top_k)
    if not results:
        typer.echo("[empty] 无匹配 Pattern（规律库为空或过滤过严；先 replay + extract-pattern 建库）")
        return
    for p, score in results:
        typer.echo(
            f"[{score:.3f}] {p.source_replay_id} | {p.shock_dimension.value}/{p.impact_direction.value} | "
            f"{p.event_type} | {p.event_summary[:40]}"
        )


@app.command("refresh-sw")
def refresh_sw(
    db: str = typer.Option(_DEFAULT_DB, "--db"),
) -> None:
    """预拉全部申万三级成分股到本地缓存（一次性，后续复盘零 tushare 网络）。"""
    from .adapters.tushare_source import TushareSource

    src = TushareSource()
    l3 = src._load_l3()
    if not l3:
        typer.echo("[fail] 无法加载申万三级清单（检查 .env TUSHARE_TOKEN）")
        raise typer.Exit(1)
    total = len(l3)
    typer.echo(f"[refresh-sw] 预拉 {total} 个申万三级行业成分股...")
    for i, ind in enumerate(l3):
        src._members(ind["index_code"])
        if (i + 1) % 50 == 0 or i == total - 1:
            typer.echo(f"  进度: {i + 1}/{total}")
    typer.echo(
        f"[done] {len(src._member_cache)} 行业成分股已缓存 → cache/tushare/sw_members.pkl\n"
        f"  后续 eventreplay replay 将零 tushare 网络调用"
    )


@app.command("forward")
def forward(
    desc: str = typer.Option(..., "--desc", help="事件描述"),
    date: str = typer.Option(..., "--date", help="事件日 YYYY-MM-DD"),
    mode: str = typer.Option("pre", "--mode", help="pre=盘前前瞻 / post=盘后校准"),
    top_k: int = typer.Option(3, "--top-k", help="检索相似 Pattern 数"),
    event_id: str = typer.Option("forward", "--event-id", help="事件 id（报告文件名）"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model", help="Claude 模型别名"),
    market: str = typer.Option("A股", "--market", help="A股/港股"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="SQLite 路径"),
    feishu: str | None = typer.Option(_DEFAULT_FEISHU, "--feishu-webhook", help="飞书 webhook URL（默认读 .env FEISHU_WEBHOOK）"),
) -> None:
    """Phase 3：前瞻映射——检索相似历史 Pattern，预判受益/受损标的 + 预期 CAR 区间。"""
    typer.echo(f"[forward] {desc[:40]}... date={date} market={market} mode={mode} top_k={top_k}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    if market == "港股":
        from .adapters.hk_quote_adapter import HKQuoteAdapter
        from .adapters.hk_stock_master import HKStockMaster
        qa = HKQuoteAdapter()
        sm = HKStockMaster()
    else:
        qa = AkshareQuoteAdapter()
        sm = StockMaster()
    storage = Storage(db)
    embedder = Embedder()
    ps = PatternService(storage, reasoner, embedder)
    orch = ForwardOrchestrator(reasoner, qa, sm, ps, storage=storage, market=market)
    markdown, path = orch.forward(
        description=desc, date_str=date, mode=mode, top_k=top_k, event_id=event_id
    )
    typer.echo(f"[done] 前瞻报告：{path}")
    # 推送飞书（如果配置了 webhook）
    if feishu:
        from .pipeline.push_channel import FeishuChannel
        ok = FeishuChannel(feishu).push(title=desc[:40], markdown=markdown, event=None)  # type: ignore[arg-type]
        typer.echo(f"[feishu] 推送{'成功' if ok else '失败'}")


@app.command("mine-events")
def mine_events(
    start: str = typer.Option(..., "--start", help="开始日期 YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="结束日期 YYYY-MM-DD"),
    industry: str | None = typer.Option(None, "--industry", help="行业过滤关键词"),
    shock_dimension: str | None = typer.Option(None, "--shock-dimension", help="供给/需求/政策/流动性/情绪"),
    top_n: int = typer.Option(5, "--top", help="最终选取数（默认5）"),
    raw_top: int = typer.Option(20, "--raw-top", help="阶段1广撒网候选数"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model", help="Claude 模型别名"),
    market: str = typer.Option("A股", "--market", help="A股/港股"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="SQLite 路径"),
) -> None:
    """Phase 4 功能2：多阶段挖掘候选事件（LLM列候选→硬过滤→LLM评估），筛选过程可追溯。"""
    typer.echo(f"[mine-events] {start}~{end} market={market} top={top_n} raw_top={raw_top}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    storage = Storage(db)
    miner = HistoricalEventMiner(reasoner, storage)
    result = miner.mine(
        start_date=start, end_date=end, top_n=top_n, industry=industry,
        shock_dimension=shock_dimension, raw_top=raw_top, market=market, echo=typer.echo,
    )
    typer.echo(
        f"[done] 最终 {len(result['final'])} 候选"
        f"（raw {result['raw_count']} → 过滤剔 {result['filtered_out']} → LLM剔 {result['llm_rejected']}）"
    )
    typer.echo(f"  最终: {result['paths']['final']}")
    typer.echo(f"  原始: {result['paths']['raw']}")
    typer.echo(f"  过滤: {result['paths']['filtered']}")
    typer.echo(f"  审核编辑后跑: eventreplay batch-replay --job-file {result['paths']['final']}")


@app.command("batch-replay")
def batch_replay(
    job_file: str = typer.Option(..., "--job-file", help="mining_candidates json 文件"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model", help="Claude 模型别名"),
    benchmark: str = typer.Option(_DEFAULT_BENCHMARK, "--benchmark"),
    market: str = typer.Option("A股", "--market", help="A股/港股"),
    db: str = typer.Option(_DEFAULT_DB, "--db"),
    sleep: float = typer.Option(0.0, "--sleep", help="每个 replay 间隔秒（限流）"),
) -> None:
    """Phase 4 功能2：批量 replay + extract-pattern 建库。"""
    from .pipeline.batch_replay import BatchReplayRunner, read_candidates
    from .pipeline.event_dedup import EventDedup

    typer.echo(f"[batch-replay] {job_file} market={market}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    if market == "港股":
        from .adapters.hk_quote_adapter import HKQuoteAdapter
        from .adapters.hk_stock_master import HKStockMaster
        qa = HKQuoteAdapter()
        sm = HKStockMaster()
        benchmark = benchmark if benchmark != _DEFAULT_BENCHMARK else "HSI"
    else:
        qa = AkshareQuoteAdapter()
        sm = StockMaster()
    storage = Storage(db)
    embedder = Embedder()
    ps = PatternService(storage, reasoner, embedder)
    orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, market=market)
    dedup = EventDedup(storage)
    runner = BatchReplayRunner(orch, ps, dedup, benchmark=benchmark)
    cands = read_candidates(job_file)
    typer.echo(f"[load] {len(cands)} 候选事件")
    prog = runner.run(cands, sleep_s=sleep)
    typer.echo(f"[done] {prog}")


@app.command("monitor")
def monitor(
    interval: int = typer.Option(_DEFAULT_INTERVAL, "--interval", help="采集间隔（分钟，默认读 .env MONITOR_INTERVAL）"),
    webhook: str | None = typer.Option(None, "--push-webhook", help="企微/钉钉 webhook URL"),
    feishu: str | None = typer.Option(_DEFAULT_FEISHU, "--feishu-webhook", help="飞书 webhook URL（默认读 .env FEISHU_WEBHOOK）"),
    significance: int = typer.Option(3, "--significance", help="重大性阈值（≥）"),
    a_share: int = typer.Option(4, "--a-share", help="A股可达性阈值（≥）"),
    surprise: float = typer.Option(3, "--surprise", help="突发性+超预期性阈值（均值≥，0=不过滤，默认3=只前瞻突发/超预期事件）"),
    impact: int = typer.Option(0, "--impact", help="影响规模阈值（≥，0=不过滤）"),
    source: str = typer.Option("multi", "--source", help="akshare/jiuyan/cctv/cls/jin10/multi"),
    model: str = typer.Option("haiku", "--model", help="Claude 模型别名（默认 haiku，forward_mapping sonnet 网关超时）"),
    db: str = typer.Option(_DEFAULT_DB, "--db"),
) -> None:
    """Phase 4 功能1：定时监测新闻 → 高分事件 forward → 推送。"""
    import time

    from .adapters.news_adapter import (
        AkshareNewsAdapter, CLSNewsAdapter, CCTVNewsAdapter,
        Jin10NewsAdapter, JiuyanNewsAdapter, MultiSourceNewsAdapter,
    )
    from .pipeline.event_dedup import EventDedup
    from .pipeline.monitoring_pusher import MonitoringPusher
    from .pipeline.nodes.event_extractor import EventExtractor
    from .pipeline.push_channel import FeishuChannel, FileChannel, WebhookChannel

    typer.echo(f"[monitor] interval={interval}min source={source} sig>={significance} a_share>={a_share}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    qa = AkshareQuoteAdapter()
    sm = StockMaster()
    storage = Storage(db)
    embedder = Embedder()
    ps = PatternService(storage, reasoner, embedder)
    fwd_orch = ForwardOrchestrator(reasoner, qa, sm, ps, storage=storage)
    news = {
        "akshare": AkshareNewsAdapter(),
        "jiuyan": JiuyanNewsAdapter(),
        "cctv": CCTVNewsAdapter(),
        "cls": CLSNewsAdapter(),
        "jin10": Jin10NewsAdapter(),
        "multi": MultiSourceNewsAdapter([
            Jin10NewsAdapter(), AkshareNewsAdapter(), CCTVNewsAdapter(), CLSNewsAdapter(),
        ]),
    }.get(source, AkshareNewsAdapter())
    extractor = EventExtractor(reasoner, storage)
    dedup = EventDedup(storage)
    channels = [FileChannel()]
    if webhook:
        channels.append(WebhookChannel(webhook))
    if feishu:
        channels.append(FeishuChannel(feishu))
    pusher = MonitoringPusher(news, extractor, fwd_orch, dedup, channels)
    try:
        while True:
            pusher.run_once(significance_min=significance, a_share_min=a_share, surprise_min=surprise, impact_min=impact)
            typer.echo(f"[monitor] sleep {interval}min... (Ctrl-C 停止)")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        typer.echo("[monitor] stopped")


if __name__ == "__main__":
    app()
