# Phase 5 Spec：港股市场接入（HK Market）

- **状态**：已实现（2026-10-19 状态回写；设计与实现的偏离见文末）
- **日期**：2026-07-04
- **所属总计划**：[2026-06-30-event-replay-master-plan.md](./2026-06-30-event-replay-master-plan.md) 的 Phase 5
- **依赖**：Phase 1（ReplayOrchestrator / QuoteAdapter / StockMaster / limits / measure_stock）；Phase 2/3（Pattern / Forward，复用）
- **关键决策**：① 多市场共存（Event/Stock 加 market 字段，pipeline 按 market 路由数据源）② 港股无涨跌停（Board.HK_MAIN 返回 None）③ 港股行业用 company_profile_em 所属行业（恒生分类）④ 港股基准 HSI 默认 + HSTECH 科技事件可选 ⑤ 港股代码 5 位 ⑥ 港股通成分股用 stock_hk_spot 全量替代（东财 ggt 不可达）⑦ MVP 不限港股通南向（假设全港股可交易）

---

## 1. 目标与范围

### 1.1 目标

在现有 A 股系统基础上接入港股，支持港股事件复盘 + 前瞻映射。复用 Phase 1/2/3 pipeline，仅替换数据层 + 适配规则。

### 1.2 范围

- ✅ **HKQuoteAdapter**：港股行情（stock_hk_daily 后复权 + stock_hk_index_daily_sina 指数）
- ✅ **HKStockMaster**：港股主数据（stock_hk_spot 2787只 + stock_hk_company_profile_em 公司资料/行业/上市日）
- ✅ **Board.HK_MAIN**：港股板块枚举 + limits 港股规则（无涨跌停）
- ✅ **多市场路由**：Event/Stock 加 market 字段，pipeline 按 market 选 QuoteAdapter/StockMaster
- ✅ **CLI --market hk**：replay/forward/mine-events/batch-replay 支持港股【2026-10-19 回写：实际 CLI 只接受 --market 港股】
- ✅ 港股代码格式（5位，如 00700）+ to_hk_symbol 转换

### 1.3 非目标

- 港股 GEM 创业板（MVP 只做主板）
- 港股通南向限制（MVP 假设全港股可交易，不限港股通名单）
- 港股衍生品/窝轮/牛熊证
- 美股（Phase 5 只港股；美股留后续）
- 港股实时 tick（仍日线）

---

## 2. 多市场架构

```
Event(market="A股"|"港股")
  ↓ 按 market 路由
┌──────────────────┬──────────────────┐
│ A股               │ 港股              │
│ AkshareQuoteAdapter │ HKQuoteAdapter   │
│ StockMaster       │ HKStockMaster    │
│ Board: 主板/创业板/科创板/北交所 │ Board: HK_MAIN │
│ 涨跌停 limits     │ 无涨跌停(None)    │
│ 申万行业          │ 恒生行业          │
│ 基准 sh000300     │ 基准 HSI/HSTECH   │
└──────────────────┴──────────────────┘
  ↓ 复用 Phase 1/2/3 pipeline
EventIngest → IndustryChain → CandidateGen → ImpactMeasurement
→ Attribution → Synthesis → Report（replay）
或 ForwardOrchestrator（forward）
```

pipeline 节点逻辑不变，按 market 选数据源 + 适配规则。

---

## 3. 数据模型

### 3.1 Board 枚举（common.py）

```python
class Board(str, Enum):
    MAIN = "主板"
    CREATIVE = "创业板"
    STAR = "科创板"
    BSE = "北交所"
    HK_MAIN = "港股主板"  # 新增
```

### 3.2 Stock schema

```python
class Stock(BaseModel):
    code: str  # A股6位 / 港股5位（如 00700）
    name: str
    market: str = "A股"  # "A股" | "港股"
    board: Board | None = None
    sw_industry_l1: str | None = None  # A股申万
    hk_industry: str | None = None  # 港股恒生行业（新增）
    listing_date: date | None = None
    is_st: bool = False
    historical_names: list[str] = []
```

### 3.3 Event schema

```python
class Event(BaseModel):
    # ... 现有字段 ...
    market: str = "A股"  # 新增："A股" | "港股"，路由数据源
```

### 3.4 港股代码格式

- 5 位数字（如 00700 腾讯、09988 阿里巴巴）
- `to_hk_symbol(code)`：5 位代码 → akshare 港股符号（stock_hk_daily 用 5 位代码，无需 sh/sz 前缀）

---

## 4. 新增组件

### 4.1 HKQuoteAdapter

- **行情**：`stock_hk_daily(symbol="00700", adjust="hfq")` → 日线后复权（date/open/high/low/close/volume）
- **指数**：`stock_hk_index_daily_sina(symbol="HSI")` → 恒生指数日线；`HSTECH` 恒生科技
- **交易日历**：`tool_trade_date_hist_sina`（与 A 股共用）
- **接口**：同 A 股 QuoteAdapter（daily_adjusted / index_daily / trade_dates）
- **基准**：默认 HSI；科技事件可选 HSTECH（CLI --benchmark HSTECH）

### 4.2 HKStockMaster

- **主数据列表**：`stock_hk_spot()` → 2787 只港股（代码/中英文名/最新价）
- **公司资料**：`stock_hk_company_profile_em(symbol)` → 17 字段（含**所属行业**/公司上市日期/公司概况）
- **resolve(identifier)**：5位代码或名称 → ResolvedStock（同 A 股反幻觉）
- **is_real_at(code, on_date)**：用 company_profile_em 上市日判断
- **constituents(industry)**：按恒生行业关键词过滤（替代 A 股申万成分股）
- **加载**：stock_hk_spot 一次性加载（2787只，小），company_profile_em 按需懒加载（避免 2787 次调用）

### 4.3 港股行业分类

- 源：`stock_hk_company_profile_em` 的"所属行业"（如腾讯=咨询服务）
- 存 Stock.hk_industry
- CandidateGen/IndustryChain 用 hk_industry（替代申万）

---

## 5. 适配

### 5.1 limits.py 港股规则

```python
if b == Board.HK_MAIN.value:
    return None  # 港股无涨跌停
```

daily_limit_pct 对 HK_MAIN 返回 None；limit_hit 对 None 返回 None（不判定涨跌停）。

### 5.2 measure_stock 港股

measure_stock 已支持 board 参数。港股 board=HK_MAIN → limits 返回 None → 不计涨跌停。其他逻辑（CAR/市场模型）不变。基准用 HSI/HSTECH。

### 5.3 pipeline 多市场路由

ReplayOrchestrator / ForwardOrchestrator 构造时按 market 选 QuoteAdapter/StockMaster：
```python
if market == "港股":
    qa = HKQuoteAdapter()
    sm = HKStockMaster()
    benchmark = "HSI"
else:
    qa = AkshareQuoteAdapter()
    sm = StockMaster()
    benchmark = "sh000300"
```
EventIngest 的 LLM prompt 加 market 提示（港股事件 → 港股标的）。CandidateGen 用对应 StockMaster。

---

## 6. CLI

```bash
# 港股复盘
eventreplay replay --desc "2023年1月腾讯大股东减持" --date 2023-01-25 --market hk --benchmark HSI

# 港股前瞻
eventreplay forward --desc "美联储降息港股受益" --date 2024-09-19 --market hk

# mine-events / batch-replay 也支持 --market hk（挖掘港股事件建库）
```

> 【2026-10-19 回写：上述 CLI 示例中的 `--market hk` 实际只接受 `--market 港股`（CLI 仅识别中文字面量；hk 别名未实现，会静默落入 A 股分支）。】

---

## 7. 复用 vs 新增

| 组件 | 来源 | 改动 |
|---|---|---|
| ReplayOrchestrator / ForwardOrchestrator | Phase 1/3 复用 | 加 market 路由数据源 |
| pipeline 7 节点 | Phase 1 复用 | 无（按 market 用对应 adapter） |
| PatternService / ForwardMapping | Phase 2/3 复用 | 无（规律库跨市场共用，或按市场分库） |
| Event / Stock schema | Phase 1 改 | Event 加 market；Stock 加 hk_industry |
| Board 枚举 | Phase 1 改 | 加 HK_MAIN |
| limits.py | Phase 1 改 | HK_MAIN 返回 None |
| **HKQuoteAdapter** | **新增** | 港股行情 |
| **HKStockMaster** | **新增** | 港股主数据 |
| **to_hk_symbol** | **新增** | 5位代码转换 |

---

## 8. 测试策略

| 层 | 方法 |
|---|---|
| HKQuoteAdapter | mock akshare → 断言日线/指数；真实 smoke stock_hk_daily/index_daily_sina 可达 |
| HKStockMaster | mock stock_hk_spot + company_profile_em → 断言 resolve/is_real_at/constituents |
| limits 港股 | Board.HK_MAIN → None（无涨跌停）|
| measure_stock 港股 | 合成港股行情 + HSI 基准 → 断言 CAR（无涨跌停计数） |
| 多市场路由 | mock market="港股" → 断言用 HKQuoteAdapter/HKStockMaster |
| 端到端港股 replay | mock Reasoner + 真实 HKQuoteAdapter → 港股报告产出 |

---

## 9. 非功能与风险

| 项 | 处理 |
|---|---|
| 港股代码 5 位 vs A 股 6 位 | to_hk_symbol 转换；StockMaster 按 market 区分 |
| 港股无涨跌停 | limits 返回 None；measure_stock 不计涨跌停 |
| 港股行业分类粗 | company_profile_em 所属行业（恒生分类，比申万粗）；MVP 够用 |
| company_profile_em 按需加载 | 2787 只全量调慢；resolve 时懒加载 + 缓存 |
| 港股通成分股不可达 | 用 stock_hk_spot 全量 2787 只作标的池 |
| 港股交易日与 A 股差异 | MVP 共用 tool_trade_date_hist_sina（港股交易日略有不同如半天市，MVP 忽略）|
| 规律库跨市场 | Pattern 不区分市场（港股事件 Pattern 与 A 股共库）；或后续按市场分库 |

---

## 10. 决策日志

- **多市场共存（Event/Stock 加 market）**：用户裁定；pipeline 按 market 路由数据源，复用 Phase 1/2/3 节点
- **港股无涨跌停**：史实；Board.HK_MAIN 返回 None，measure_stock 不计涨跌停
- **港股行业用 company_profile_em 所属行业**：spike 确认（stock_hk_spot 行业字段常空，company_profile_em 有）
- **港股基准 HSI 默认 + HSTECH 科技事件可选**：工程判断；HSI 全市场，HSTECH 科技板块
- **港股通成分股用全量 spot 替代**：spike 确认东财 ggt 不可达；2787 只全量作标的池
- **MVP 不限港股通南向**：YAGNI；假设全港股可交易，不限港股通名单
- **规律库跨市场共用**：MVP 简化；Pattern 不区分市场（港股事件提炼的 Pattern 也可被 A 股 forward 检索，反之亦然）

---

## 实现期状态回写（2026-10-19）

> 本节为 Phase 5 实现完成后的回写；上文为 2026-07-04 的设计历史，未改写。

- **已实现并有单测**：港股行情（`stock_hk_daily` 后复权 + `stock_hk_index_daily_sina`）、港股主数据（`stock_hk_spot` + `stock_hk_company_profile_em`）、`Board.HK_MAIN` 无涨跌停、HSI/HSTECH 基准、`--market 港股` 多市场路由（CLI 仅识别中文字面量“港股”；hk 别名未实现，会静默落入 A 股分支）已实现并有单测覆盖；已产出港股复盘报告 `reports/hk_tencent_dump.md`（腾讯大股东减持事件；该事件未提炼 Pattern）。
- **未做（维持原非目标）**：港股 GEM 创业板、港股通南向限制、港股衍生品/窝轮/牛熊证、美股均未接入；港股行情仍为日线（无实时 tick）。
- **口径**：港股事件与 A 股共用同一 Pattern 库（原 §9“规律库跨市场”设计口径），未按市场分库。
