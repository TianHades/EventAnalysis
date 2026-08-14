# Phase 5 实施计划：港股市场接入

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
