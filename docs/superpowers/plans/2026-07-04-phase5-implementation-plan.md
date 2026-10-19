# Phase 5 实施计划：港股市场接入

- **状态**：**P1–P5 全部实现**（2026-10-19 状态回写；逐步骤结果见文末 §实现状态）。
- **依据 spec**：[phase5-hk-market.md](../specs/2026-07-04-phase5-hk-market.md)
- **方法**：TDD + 增量；复用 Phase 1/2/3 pipeline，新增港股数据层 + 适配
- **港股数据源**：spike 确认 sina 源可用（stock_hk_daily / stock_hk_spot / stock_hk_index_daily_sina / company_profile_em）

## 步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| **P1 schema + Board** | `Board.HK_MAIN`（common.py）+ `Stock.hk_industry` + `Event.market` | Board 枚举含港股主板；Stock/Event 港股字段可构造；现有测试不破坏 |
| **P2 HKQuoteAdapter** | `adapters/hk_quote_adapter.py`（stock_hk_daily 后复权 + stock_hk_index_daily_sina HSI/HSTECH + trade_dates） | mock akshare → 日线/指数 NewsItem；真实 smoke stock_hk_daily/index_daily_sina 可达 |
| **P3 HKStockMaster** | `adapters/hk_stock_master.py`（stock_hk_spot 2787只 + company_profile_em 懒加载；resolve/is_real_at/constituents） | mock → resolve(00700/腾讯) 返回港股标的；is_real_at 用上市日；constituents 按恒生行业过滤 |
| **P4 limits + measure_stock 适配** | limits.py `Board.HK_MAIN → None`；measure_stock 港股不计涨跌停 | Board.HK_MAIN → daily_limit_pct 返回 None；measure_stock 港股合成行情 → CAR 正确 + 涨跌停 0 |
| **P5 多市场路由 + CLI** | Replay/ForwardOrchestrator 按 market 选 QuoteAdapter/StockMaster + 基准；CLI `--market hk` | mock market="港股" → 用 HKQuoteAdapter/HKStockMaster；CLI --market hk 注册；端到端港股 replay mock 跑通 |

## 执行顺序

P1（schema）→ P2（HKQuoteAdapter）→ P3（HKStockMaster）→ P4（limits 适配）→ P5（路由 + CLI）

## 关键复用

- Phase 1：ReplayOrchestrator / measure_stock / limits / Event / Stock
- Phase 2/3：PatternService / ForwardOrchestrator（规律库跨市场共用）
- pipeline 7 节点逻辑不变，按 market 选数据源

## 测试要点

- HKQuoteAdapter：mock akshare + 真实 smoke 两源可达
- HKStockMaster：resolve 反查（5位代码/中英文名）+ is_real_at（company_profile_em 上市日）+ constituents（恒生行业过滤）
- limits：HK_MAIN 返回 None（无涨跌停），limit_hit(None) 返回 None
- measure_stock：港股合成行情 + HSI 基准 → CAR 正确，limit_up/down_days=0
- 多市场路由：market="港股" → HKQuoteAdapter/HKStockMaster；market="A股" → 原组件（不破坏）
- 端到端港股 replay：mock Reasoner + 真实 HKQuoteAdapter → 港股报告产出

## 实现状态（2026-10-19 回写）

> 2026-10-19 逐步骤复核（原步骤表保留在上方）。全局事实见 [总计划 §10](../specs/2026-06-30-event-replay-master-plan.md) 与 [Phase 5 spec 文末回写](../specs/2026-07-04-phase5-hk-market.md)。

| 步骤 | 结果 | 落地证据与说明 |
|---|---|---|
| **P1 schema + Board** | **完成** | `schema/common.py:58` `Board.HK_MAIN`；`schema/stock.py:15` `market`、`:20` `hk_industry`；`schema/event.py:38` `Event.market` |
| **P2 HKQuoteAdapter** | **完成** | `adapters/hk_quote_adapter.py:46`；`daily_adjusted`（`:118`）/ `index_daily`（`:138`，HSI/HSTECH）/ `trade_dates`（`:155`） |
| **P3 HKStockMaster** | **完成** | `adapters/hk_stock_master.py:50`；`resolve`（`:170`，5 位代码/中英文名）/ `is_real_at`（`:204`）/ `constituents`（`:216`，恒生行业过滤） |
| **P4 limits + measure_stock 适配** | **完成** | `measurement/limits.py:61`：`Board.HK_MAIN` → 无涨跌停（`daily_limit_pct` 返回 None）；`limit_hit(None)` 返回 None；港股度量不计涨跌停 |
| **P5 多市场路由 + CLI** | **完成（有参数口径缺口）** | `cli.py:73-89` `make_adapters` 按 market 切 HKQuoteAdapter/HKStockMaster 与基准（港股未显式指定时切 HSI）；`--market` 覆盖 `replay`（`:100`）/ `forward`（`:207`）/ `mine-events`（`:242`）/ `batch-replay`（`:274`）；`orchestrator.py:136`、`forward_orchestrator.py:92` 给 Event 打市场标记。**注意**：market 实际只接受字面量 `港股`（`cli.py:80` 以 `market == "港股"` 判定），原验收/文档示例的 `--market hk` 不会被识别，会静默落入 A 股分支 |

### 现状与缺口

- 港股链路有代码 + 单测覆盖（`tests/test_hk_quote_adapter.py`、`tests/test_hk_stock_master.py`）；实际产物为 1 份港股复盘报告 `reports/hk_tencent_dump.md`（腾讯大股东减持；该事件未提炼 Pattern，故 58 个 Pattern 中无港股来源）。
- **未做（维持原非目标）**：港股 GEM 创业板、港股通南向限制、港股衍生品/窝轮/牛熊证、美股未接入；港股行情仍为日线（无实时 tick）；港股真实端到端本轮未复跑。
- **口径**：港股事件与 A 股共用同一 Pattern 库（未按市场分库），与 spec 设计一致。
