"""命令行入口：eventreplay replay / extract-pattern / find-similar / forward。

启动时自动加载根目录 .env（凭证 + 默认值）；--model/--benchmark/--db 缺省读环境变量。
"""

from __future__ import annotations

import os
from datetime import date

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
_DEFAULT_FEISHU = os.getenv("FEISHU_WEBHOOK", "") or None
_DEFAULT_INTERVAL_FALLBACK = 240  # MONITOR_INTERVAL/--interval 坏值的回退值


def _parse_interval_minutes(raw: str | None) -> tuple[int, str | None]:
    """解析采集间隔（分钟）→ (minutes, 坏值告警|None)。

    坏值（.env 写错/CLI 传错）不再让模块导入崩溃：回退 240 并返回告警文案。
    """
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_INTERVAL_FALLBACK, None
    try:
        return int(str(raw).strip()), None
    except ValueError:
        return _DEFAULT_INTERVAL_FALLBACK, (
            f"MONITOR_INTERVAL/--interval 值非法: {raw!r}（需整数分钟），已回退 {_DEFAULT_INTERVAL_FALLBACK}"
        )


def _require_iso_date(value: str, opt: str) -> str:
    """校验 YYYY-MM-DD（fromisoformat 严格解析），坏值抛 typer.BadParameter 带示例。"""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(
            f"日期格式需为 YYYY-MM-DD（如 2024-01-15），收到 {value!r}", param_hint=opt
        )
    return value


def _require_enum(value: str | None, enum_cls, opt: str):
    """校验枚举参数（空直通 None），坏值抛 BadParameter 并列出全部合法取值。"""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        valid = "、".join(e.value for e in enum_cls)
        raise typer.BadParameter(f"合法取值：{valid}", param_hint=opt)


# --market 别名 → 规范中文值（hk/HK 等不再静默落入 A 股分支用错数据源）
_MARKET_ALIASES = {
    "a股": "A股",
    "a": "A股",
    "cn": "A股",
    "ashare": "A股",
    "a-share": "A股",
    "港股": "港股",
    "hk": "港股",
    "hkex": "港股",
    "hongkong": "港股",
}
_MARKET_VALID_HINT = "合法取值：A股（别名 a/cn/ashare）、港股（别名 hk/hkex/hongkong）"


def normalize_market(raw: str | None) -> str:
    """把 --market 原始值归一为规范中文值（A股/港股）；未命中别名抛 ValueError。"""
    key = (raw or "").strip().lower()
    try:
        return _MARKET_ALIASES[key]
    except KeyError:
        raise ValueError(_MARKET_VALID_HINT) from None


def _require_market(value: str) -> str:
    """校验并归一 --market（非法值抛 typer.BadParameter，不静默落 A 股分支）。"""
    try:
        return normalize_market(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--market") from None


def make_adapters(market: str, benchmark: str | None = None):
    """按市场装配适配器 → (quote_adapter, stock_master, benchmark)。

    统一 replay/forward/batch-replay 三处"港股分支 + benchmark 切 HSI"的装配逻辑：
    入口先归一别名（hk/HK→港股），A股默认 sh000300；港股用 HK 适配器，
    benchmark 未显式指定（或仍是 A 股默认值）时切 HSI。
    """
    market = normalize_market(market)  # 防御库内调用：别名与 CLI 口径一致
    if market == "港股":
        from .adapters.hk_quote_adapter import HKQuoteAdapter
        from .adapters.hk_stock_master import HKStockMaster

        qa, sm = HKQuoteAdapter(), HKStockMaster()
        bench = benchmark if benchmark and benchmark != _DEFAULT_BENCHMARK else "HSI"
    else:
        qa, sm = AkshareQuoteAdapter(), StockMaster()
        bench = benchmark or _DEFAULT_BENCHMARK
    return qa, sm, bench


@app.command()
def replay(
    desc: str = typer.Option(..., "--desc", help="事件描述"),
    date: str = typer.Option(..., "--date", help="事件公告日 YYYY-MM-DD"),
    reaction_date: str | None = typer.Option(None, "--reaction-date", help="市场首反应日"),
    benchmark: str = typer.Option(_DEFAULT_BENCHMARK, "--benchmark", help="基准指数（A股 sh000300 / 港股 HSI）"),
    event_id: str = typer.Option("evt", "--event-id", help="事件 id（报告文件名）"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model", help="Claude 模型别名"),
    market: str = typer.Option("A股", "--market", help="A股（默认）/港股；也可用 hk 等别名"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="SQLite 路径"),
) -> None:
    """Phase 1：对单个历史事件跑完整复盘流水线。"""
    market = _require_market(market)  # 非法/别名先归一，再构造 LLM 与适配器
    date = _require_iso_date(date, "--date")
    if reaction_date is not None:
        reaction_date = _require_iso_date(reaction_date, "--reaction-date")
    typer.echo(f"[replay] {desc[:40]}... date={date} market={market} model={model}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    qa, sm, benchmark = make_adapters(market, benchmark)
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
    sd = _require_enum(shock_dimension, ShockDimension, "--shock-dimension")
    idir = _require_enum(direction, ImpactDirection, "--direction")
    embedder = Embedder()
    storage = Storage(db)
    svc = PatternService(storage, reasoner=None, embedder=embedder)  # type: ignore[arg-type]
    results, degraded = svc.find_similar(desc, shock_dimension=sd, impact_direction=idir, industry=industry, top_k=top_k)
    if degraded:
        typer.echo("[warn] 向量检索不可用（embedding 失败），以下为纯结构化过滤结果，相似度不可比")
    if not results:
        if degraded:
            typer.echo("[empty] 检索不可用且无结构化命中（不代表无相似 Pattern）")
        else:
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
    market: str = typer.Option("A股", "--market", help="A股（默认）/港股；也可用 hk 等别名"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="SQLite 路径"),
    feishu: str | None = typer.Option(_DEFAULT_FEISHU, "--feishu-webhook", help="飞书 webhook URL（默认读 .env FEISHU_WEBHOOK）"),
) -> None:
    """Phase 3：前瞻映射——检索相似历史 Pattern，预判受益/受损标的 + 预期 CAR 区间。"""
    market = _require_market(market)  # 非法/别名先归一，再构造 LLM 与适配器
    date = _require_iso_date(date, "--date")
    if mode not in ("pre", "post"):
        raise typer.BadParameter("合法取值：pre（盘前前瞻）、post（盘后校准）", param_hint="--mode")
    typer.echo(f"[forward] {desc[:40]}... date={date} market={market} mode={mode} top_k={top_k}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    qa, sm, _ = make_adapters(market)
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
    market: str = typer.Option("A股", "--market", help="A股（默认）/港股；也可用 hk 等别名"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="SQLite 路径"),
) -> None:
    """Phase 4 功能2：多阶段挖掘候选事件（LLM列候选→硬过滤→LLM评估），筛选过程可追溯。"""
    market = _require_market(market)  # 非法/别名先归一，再构造 LLM 与 Storage
    typer.echo(f"[mine-events] {start}~{end} market={market} top={top_n} raw_top={raw_top}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    storage = Storage(db)
    miner = HistoricalEventMiner(reasoner, storage)
    result = miner.mine(
        start_date=start, end_date=end, top_n=top_n, industry=industry,
        shock_dimension=shock_dimension, raw_top=raw_top, market=market, echo=typer.echo,
    )
    # 阶段1 LLM 失败时 mine() 返回 {"final": [], ..., "paths": {}}（未落盘）→ 友好报错而非 KeyError
    paths = result.get("paths") or {}
    if not paths.get("final"):
        typer.echo("[fail] 阶段1 LLM 未能产出候选，未生成文件（见上方日志）")
        raise typer.Exit(1)
    typer.echo(
        f"[done] 最终 {len(result['final'])} 候选"
        f"（raw {result['raw_count']} → 过滤剔 {result['filtered_out']} → LLM剔 {result['llm_rejected']}）"
    )
    typer.echo(f"  最终: {paths['final']}")
    typer.echo(f"  原始: {paths.get('raw', '-')}")
    typer.echo(f"  过滤: {paths.get('filtered', '-')}")
    typer.echo(f"  审核编辑后跑: eventreplay batch-replay --job-file {paths['final']}")


@app.command("batch-replay")
def batch_replay(
    job_file: str = typer.Option(..., "--job-file", help="mining_candidates json 文件"),
    model: str = typer.Option(_DEFAULT_MODEL, "--model", help="Claude 模型别名"),
    benchmark: str = typer.Option(_DEFAULT_BENCHMARK, "--benchmark"),
    market: str = typer.Option("A股", "--market", help="A股（默认）/港股；也可用 hk 等别名"),
    db: str = typer.Option(_DEFAULT_DB, "--db"),
    sleep: float = typer.Option(0.0, "--sleep", help="每个 replay 间隔秒（限流）"),
) -> None:
    """Phase 4 功能2：批量 replay + extract-pattern 建库。"""
    market = _require_market(market)  # 非法/别名先归一，再构造 LLM 与适配器
    from .pipeline.batch_replay import BatchReplayRunner, read_candidates
    from .pipeline.event_dedup import EventDedup

    typer.echo(f"[batch-replay] {job_file} market={market}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    qa, sm, benchmark = make_adapters(market, benchmark)
    storage = Storage(db)
    embedder = Embedder()
    ps = PatternService(storage, reasoner, embedder)
    orch = ReplayOrchestrator(reasoner, qa, sm, storage=storage, market=market)
    dedup = EventDedup(storage)
    runner = BatchReplayRunner(orch, ps, dedup, benchmark=benchmark)
    cands, invalid_dates = read_candidates(job_file, echo=typer.echo)
    typer.echo(f"[load] {len(cands)} 候选事件（{invalid_dates} 个因缺/非法日期被剔除，已计入 failed）")
    prog = runner.run(cands, sleep_s=sleep)
    prog["failed"] += invalid_dates
    typer.echo(f"[done] {prog}")


@app.command("monitor")
def monitor(
    interval: str | None = typer.Option(None, "--interval", help="采集间隔分钟（整数；缺省读 .env MONITOR_INTERVAL，默认 240）"),
    webhook: str | None = typer.Option(None, "--push-webhook", help="企微/钉钉 webhook URL"),
    feishu: str | None = typer.Option(_DEFAULT_FEISHU, "--feishu-webhook", help="飞书 webhook URL（默认读 .env FEISHU_WEBHOOK）"),
    significance: int = typer.Option(3, "--significance", help="重大性阈值（≥）"),
    a_share: int = typer.Option(4, "--a-share", help="A股可达性阈值（≥）"),
    surprise: float = typer.Option(3, "--surprise", help="突发性+超预期性阈值（均值≥，0=不过滤，默认3=只前瞻突发/超预期事件）"),
    impact: int = typer.Option(0, "--impact", help="影响规模阈值（≥，0=不过滤）"),
    source: str = typer.Option("multi", "--source", help="akshare/jiuyan/cctv/cls/jin10/multi"),
    model: str = typer.Option("haiku", "--model", help="Claude 模型别名（默认 haiku，forward_mapping sonnet 网关超时）"),
    screen: bool = typer.Option(True, "--screen/--no-screen", help="两级漏斗：先批量初筛再细评（默认开，--no-screen 回到逐条细评）"),
    once: bool = typer.Option(False, "--once", help="只跑一轮（采集→抽取→前瞻→推送）后退出；默认常驻循环"),
    db: str = typer.Option(_DEFAULT_DB, "--db"),
) -> None:
    """Phase 4 功能1：定时监测新闻 → 高分事件 forward → 推送。"""
    from .adapters.news_adapter import (
        AkshareNewsAdapter, CLSNewsAdapter, CCTVNewsAdapter,
        Jin10NewsAdapter, JiuyanNewsAdapter, MultiSourceNewsAdapter,
    )
    from .pipeline.event_dedup import EventDedup
    from .pipeline.monitoring_pusher import MonitoringPusher, run_monitor_loop
    from .pipeline.nodes.event_extractor import EventExtractor
    from .pipeline.nodes.news_screening import NewsScreeningNode
    from .pipeline.push_channel import FeishuChannel, FileChannel, WebhookChannel

    # 间隔解析移到命令体内：.env 写错值不至于让所有 CLI 命令在导入期崩溃
    interval_min, warn = _parse_interval_minutes(interval if interval is not None else os.getenv("MONITOR_INTERVAL"))
    if warn:
        typer.echo(f"[monitor] {warn}")
    typer.echo(f"[monitor] interval={interval_min}min source={source} sig>={significance} a_share>={a_share}")
    reasoner = ClaudeCodeProvider(model_alias=model)
    qa, sm, _ = make_adapters("A股")  # 监测当前仅 A 股；与 replay/forward 装配同源
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
    pusher = MonitoringPusher(
        news, extractor, fwd_orch, dedup, channels,
        screener=NewsScreeningNode(reasoner),  # 两级漏斗第一级（screen=False 时不用）
    )
    try:
        # 主循环体整体兜底：单轮任何异常 echo 后继续 sleep（监控的核心要求是持续运行）
        run_kwargs = dict(
            significance_min=significance, a_share_min=a_share,
            surprise_min=surprise, impact_min=impact, screen=screen,
        )
        if once:
            n = pusher.run_once(echo=typer.echo, **run_kwargs)
            typer.echo(f"[monitor] --once 单轮完成，推送 {n} 条")
        else:
            run_monitor_loop(pusher, interval_min=interval_min, echo=typer.echo, **run_kwargs)
    except KeyboardInterrupt:
        typer.echo("[monitor] stopped")


if __name__ == "__main__":
    app()
