# Phase 1 Spec：复盘 MVP

- **状态**：v3.0 已实现（2026-10-19 状态回写；实现期偏离与未落地项见文末）
- **日期**：2026-06-30
- **所属总计划**：[2026-06-30-event-replay-master-plan.md](./2026-06-30-event-replay-master-plan.md) 的 Phase 1
- **数据 spike 结果**：[2026-06-30-spike-results.md](./2026-06-30-spike-results.md)（实跑 ground truth）
- **前置**：最小 Phase 0 基座并入本阶段实现
- **v2.5 要点**：据 spike 实跑替换所有数据/CLI 推测项——主源改 sina（eastmoney/baostock 不可达）、沪深300全收益不可得→价格指数为主、申万成分股仅一级可用、`--bare` 必须（否则 .mcp.json 卡死）、GRANK 手写（见 §13）

---

## 1. 目标与范围

### 1.1 目标

给定一个历史重大事件，跑通完整 7 节点复盘流水线，产出含"产业链逻辑链 + 量化影响幅度/持续性"的复盘报告。

### 1.2 范围

最小 Phase 0 基座 + 7 节点 pipeline + 影响度量引擎（事件研究法 + A 股修正）+ markdown 报告 + CLI 单事件触发 + 5 种子事件验收夹具。MVP 种子事件聚焦 A 股视角；海外宏观留待 Phase 5/后续。

### 1.3 非目标（后续/增强）

规律库(Phase2)、前瞻映射(Phase3)、监测推送(Phase4)、多市场(Phase5)、UI；NewsAdapter(Phase4)、独立产业链图谱库；**研究级方法论**：安慰剂检验、市场状态调整、FF3/FF5 本地化、Dimson β、AR(1) 半衰期、BHAR 显著性检验、CAR 峰值回落 50% 天数。

---

## 2. 最小基座（Phase 0 并入）

> 数据源为 §4.0 spike 实跑确认（见 [spike-results](./2026-06-30-spike-results.md)）。本节为**验证后的事实**，不再是待确认推测。

| 组件 | 说明（spike 验证） |
|---|---|
| **QuoteAdapter** | **主源 sina**（eastmoney 不可达）：个股后复权日线 `stock_zh_a_daily(symbol="sh600519", adjust="hfq", ...)`（列含 date/open/high/low/close/volume/amount/**outstanding_share**/turnover）；指数日线 `stock_zh_index_daily(symbol="sh000300")`（价格指数）；交易日历 `tool_trade_date_hist_sina()`。**市值 = outstanding_share × close 推导**（spot 接口不可达）。接口化，后续若 eastmoney 恢复可切回 |
| **StockMaster** | 标的字典：基本信息（`stock_info_a_code_name` flaky → 重试 + 备用 name_code 接口）；**历史曾用名**= 深市全市场 `stock_info_sz_change_name(symbol="简称变更")`（7432 行，已验证）+ 单标的 `stock_info_change_name(symbol)`（真→rows / **假/无效→KeyError**，反查须异常捕获+双源交叉）；退市 `stock_info_sh_delist()` / `stock_info_sz_delist(symbol="终止上市公司")`（已验证）；**申万行业清单**一/二/三级 `sw_index_*_info`（已验证）；**申万成分股仅一级可用** `index_component_sw(symbol="801001")`（二/三级 KeyError）→ CandidateGen 用一级底池。用途：反查标的真实性 + 按行业检索成分股底池 |
| **全局 schema** | Pydantic：`Event`（`announcement_date`/`reaction_date`/公告时点/`episodes: EventEpisode[]`，单事件 episodes 恰 1 元素、episode_id 必填）/`Stock`/`ImpactMeasurement`（`episode_id`/`low_confidence`+reason/`limit_up_days`/`limit_down_days`/持续性子字段 `persistence_ratio_60_5`/`sig_ar_days`/`aav_days`/`peak_ar`/`max_drawdown`/`chain_node_id`/`direction`/`tier`/`rationale`/`direction_conflict`）/`AttributionResult`（见 §3.3）/`TransmissionChain`/`Replay`。`events`/`replays` 预留 `embedding_text`/`embedding_model`（Phase 2） |
| **Claude CLI 封装** | `llm/reasoner.py` 接口 + provider。调用（spike 实测可用）：`claude -p --output-format json --json-schema '<schema>' --bare --disable-slash-commands --permission-mode dontAsk --model '<alias>'`，不传 `--mcp-config`。读 `.structured_output` → Pydantic 校验（业务校验失败等同失败）→ 修复式重试（≤2 次）→ §3.2 降级。**spike 确认事实**：① `--bare` **必须用于子进程调用**——不带它 `claude -p` 会加载 `.mcp.json` 的 forge_extension MCP 而**挂起**；② `--bare` 鉴权：GLM 网关（`ANTHROPIC_AUTH_TOKEN`+`ANTHROPIC_BASE_URL`，3P provider 凭证）实跑可用；直连 Anthropic 则严格 `ANTHROPIC_API_KEY`/`apiKeyHelper`，永不读 OAuth/keychain；③ `--disable-slash-commands` 禁 skills（`--bare` 不跳 skills）；④ `--model` alias（`sonnet`）实跑可用；full-name/dated-id 留待部署确认最具体可用形式；⑤ schema 强制力来自 `--json-schema`（`--output-format json` 控封装）；⑥ 无 `--temperature` |
| **存储** | SQLite：`events`/`replays`/`measurements`/`chains`/`pipeline_runs`/`skip_log` |

> 产业链：Phase 1 为 LLM 实时推理产物。每节点输出自由文本描述 + 申万行业代码（粒度=一级，受成分股可得性约束）；StockMaster 校验存在性 + 语义匹配（§3.1）。
>
> 【2026-10-19 回写：成分股口径已由 tushare 申万三级（`refresh-sw` 预拉本地）+ ThemeIndex curated 兜底 + LLM 提议 resolve 反查替代；“粒度=一级、仅一级可用”为 v2.5 旧结论，见文末】

---

## 3. 复盘流水线（7 节点）

```
[EventIngest*] → [IndustryChainReasoning*] → [CandidateGeneration*]
   → [ImpactMeasurement#] → [AttributionFiltering*] → [TransmissionChainSynthesis*]
   → [ReportAssembly*]      (* = Claude 推理节点,  # = Python 量化节点)
```

| 节点 | 类型 | 输入 → 输出 | 关键设计 |
|---|---|---|---|
| **EventIngest** | 推理 | 事件描述/原文 → `Event`（含 `episodes[]`；未传 `--episode-dates` 时用 `--date` 构造单元素 episodes） | 每子事件各自 reaction_date/announcement_date/公告时点；类型、冲击维度、影响方向、关键实体 |
| **IndustryChainReasoning** | 推理 | `Event` → 产业链节点图（每节点：自由文本描述 + sw_industry 一级代码） | 推理上/中/下游受冲击节点+方向；StockMaster 校验代码存在性 + 描述↔行业名语义匹配（§3.1）；单节点不一致/语义不达标 → `low_confidence` 透传；整链 >50% 节点低置信 → 报告头部标注 |
| **CandidateGeneration** | 推理 | `Event` + 节点(描述+sw_industry) + **多源成分股底池** → 候选清单(每标的含 `chain_node_id`/`direction`/`rationale`，上限 40) | **三路合并**（v3.0）：① LLM 提议（by name）② **tushare 申万三级成分股**（权威，预拉缓存 `refresh-sw`）③ **curated themes.yaml**（兜底）；候选经 StockMaster（历史名双源交叉、异常捕获）反查；去重 + 封顶 40 |
| **ImpactMeasurement** | 量化 | 候选 + reaction_date → `ImpactMeasurement[]`（标的×子事件二维） | 事件研究法（§4）；剔除新股/停牌/低换手 |
| **AttributionFiltering** | 推理 | 候选(含direction) + 度量 → `AttributionResult` | **两段式（§4.3）**：(a) 按单标的经济显著性 + direction 划池（direction 与 CAR 符号冲突 → 标 `direction_conflict`/low_confidence，保留进报告但不参与强弱排序）；(b) 对两池算组合统计量作辅证/披露，不作过滤输入 |
| **TransmissionChainSynthesis** | 推理 | `{Event, 产业链图, AttributionResult}` → `TransmissionChain` | 归纳完整逻辑链（有向图） |
| **ReportAssembly** | 推理 | 全部产物 → markdown 报告 | 按 §7；**LLM 超时/失败 → Python 模板兜底**（v3.0：`render_report()` 从结构化数据确定性生成，秒级可靠） |

### 3.1 反幻觉护栏

推理节点强制 `--json-schema` + Pydantic；候选标的经 StockMaster（历史名双源交叉 + 异常捕获）反查；**产业链节点 sw_industry 语义校验：MVP 用关键词/token Jaccard + 关键词词典（不用 embedding），阈值 ≥1 核心词命中即 pass，不达标 → low_confidence（不拒绝）**；进报告标的须有量化证据。

### 3.2 降级语义（每节点明确）

| 节点 | 降级行为 |
|---|---|
| EventIngest | 失败 → 中止 Replay |
| IndustryChainReasoning | 仅整节点不可解析/全部节点映射失败才退化为"按 Event 行业标签直查"；单节点低置信仅透传 |
| CandidateGeneration | 失败 → 底池=命中节点一级成分股按成交额 top-30/方向预筛 |
| ImpactMeasurement | 单标的无数据 → 记 `skip_log` 跳过 |
| AttributionFiltering | 失败 → 保留全部候选，分级列标"未分级" |
| TransmissionChainSynthesis | 失败 → 结构化数据直出图表，叙述省略 |
| ReportAssembly | 失败 → 输出半结构化 JSON |

> 候选规模：每方向底池 ≤30，过滤后 ≤15。**某方向过滤后为 0 → 在该方向取 direction 一致的 |CAR| 最大 top-5 标"弱反应"，仅纳入 `|CAR[0,+20]| ≥ 3%`（主板）/ `≥ 6%`（创科）者；无标的达下限则报告"未发现经济上显著反应"，不塞入**。

### 3.3 AttributionResult 契约（节点5输出 → 节点6输入）

```
AttributionResult {
  beneficiary_pool[] / victim_pool[]: { stock_id, chain_node_id, direction, tier, episode_id, low_confidence, direction_conflict, rationale }
  pool_stats: { per_pool: { n, caar, grank_z, grank_p, adj_bmp_z, adj_bmp_p, disclosed: bool } }  # N<5 时 disclosed=false
  tier_definitions; industry_granularity: 一级  # 当前粒度
}
```

---

## 4. 影响度量引擎规格（ImpactMeasurement）

### 4.0 数据可用性（spike 实跑结论，见 [spike-results](./2026-06-30-spike-results.md)）

**已验证可用**：个股后复权（`stock_zh_a_daily` sina）、指数价格（`stock_zh_index_daily`）、交易日历、深市全市场改名、单标的改名、退市（沪深）、申万行业清单一/二/三级、申万**一级**成分股。
**已验证不可用/受限**：eastmoney 全系（`stock_zh_a_hist`/`index_zh_a_hist`/`stock_zh_a_spot_em`）、baostock、沪深300全收益 H00300.CSI、申万**二/三级**成分股、`stock_info_a_code_name`（flaky）。
>
> 【2026-10-19 回写：申万二/三级成分股“不可用”已被 v3.0 的 tushare 申万三级方案推翻（见 [spike-results](./2026-06-30-spike-results.md) 顶部注记）；eastmoney/baostock 不可达结论仍成立。】
**残余待定（实现期低风险）**：`--model` full-name/dated-id 最具体可用形式；`stock_info_a_code_name` 替代源。网络为外部依赖（eastmoney/baostock 不可达是环境约束，非 spec 缺陷）。

### 4.1 数据、窗口与事件日标准化

- 数据源：sina（个股后复权 + 成交量 + 指数 + 交易日历）
- **事件日标准化**：度量以 `reaction_date` 为 t=0。公告时点：盘前(<09:30)→当日；盘中(09:30–15:00)→当日但 AR[t0] 仅含部分（标注）；盘后(>15:00)→下一交易日。非交易日→下一交易日。跨时区→"北京时间次一交易日开盘前"。跨长假→按交易日历推进
- 估计窗 `[t−120, t−21]`；**t−20..t−2 为缓冲 gap**；事件窗 `{[0,+1],[0,+5],[0,+20],[0,+60],[0,+90]}` + `[-1,0]` 诊断窗。**+90 窗口**（v3.0 新增）：捕捉 late-bloomer（效应在 +60 后才显现，如中船特气 +60=+23% → +90=+106%）

### 4.2 正常收益模型

默认市场模型 `R_i = α + β·R_m`（OLS，估计窗）。**基准为沪深300价格指数（sh000300）**（全收益不可得，见 §4.7）。FF3/FF5 不进 MVP。

### 4.3 异常收益与显著性

- `AR_t = R_{i,t} − (α̂ + β̂·R_{m,t})`；`CAR(W) = Σ AR_t`
- **"显著 AR"标志（标的级，仅标的内时序计数，禁止跨标的排序）**：`|AR_t|/σ_AR > 1.96`，σ_AR = 估计窗残差标准差（简化口径）。**组合推断用另一口径**：forecast-error-adjusted（Patell/BMP），二者不混用
- **组合层面显著性**（按受益池/受损池分别算 CAAR）：**同时计算 KP(2011) GRANK 与 KP(2010) Adjusted BMP**（`Z_adj = Z_BMP/√(1+(L−1)·r̄)`）；
  - 主推断默认取 GRANK（覆盖聚集+非正态+小 N）；
  - 良性场景自适应：N≥30 且 Shapiro-Wilk 不拒绝正态 且 平均截面相关 r̄<0.1 → 主推断改取 Adjusted BMP；
  - 池 N<5 → 统计量仅记录不披露；
  - 二者结论不一致 → 报告同时披露两 p 值并标"分歧"。
  - **实现：GRANK 手写**（spike 确认 `event-study-toolkit` 不暴露可用 GRANK；按 Kolari-Pynnönen 原文 Table 2 实现调整因子 + 蒙特卡洛 size 检验）【2026-10-19 回写：GRANK/Adjusted BMP 已在 `measurement/significance.py` 实现，但归因 `attribution.py` 仍用 scipy t 检验、`PoolStat.adj_bmp_*` 恒空、报告注“简化 t 检验”；组合推断尚未接入归因，见文末】
- **单标的过滤（AttributionFiltering 第一段）**：经济显著性为主——**car_best = max|CAR| 跨 [0,+20]/[0,+60]/[0,+90] 任一窗口**（v3.0 改动：原仅看 +20，漏 late-bloomer；现 +20/+60/+90 任一窗口 ≥10%（主板）/ ≥20%（创科）即入池）；N<5 只用绝对阈值。组合统计量第二段算，仅辅证/披露
- **不产出单标的 CAR 的 t 值**（对 σ 假设敏感、不可靠）

### 4.4 幅度与持续性

- 幅度：各窗 CAR（**含 [0,+90]**）、峰值 AR、涨/跌停天数、最大回撤（不产出单标的 CAR t 值）
- 持续性（主）：长/短窗 CAR 比（`CAR[0,+60]/CAR[0,+5]`）、显著 AR 持续天数（标的内时序，禁止跨标的排序）
- 持续性（辅）：AAV 持续天数（`AAV_t=(V_t−μ_V)/σ_V`，μ/σ 取估计窗；连续 `AAV_t>2.0` 天数）
- **AR(1) 半衰期、BHAR 显著性、CAR 峰值回落 50% 天数 均移出 MVP**

**涨跌停处理**（按事件窗每日生效规则查表；日级标记聚合入 `limit_up_days`/`limit_down_days`）：

| 板块/情形 | 涨跌幅 | 生效日 |
|---|---|---|
| 主板（已上市）/ 主板新股前 5 日 | 10% / 不设限 | 始终 / 2023-04-10 起 |
| 创业板 / 创业板新股前 5 日 | 10%→20% / 不设限 | 2020-08-24 起 20% |
| 科创板 / 科创板新股前 5 日 | 20% / 不设限 | 2019-07-22 起 |
| 北交所 / 北交所新股前 5 日 | 30% / 不设限 | 2021-11-15 起 |
| ST（主板） | 5%（2026-07-06 起改 10%，按事件期版本查最新） | — |
| ST（创业板/科创板） | 仍 20% | 不降 5% |

> 主板 2023-04-10 前新股首日 +44%/-36% 按事件期版本化。涨跌停日 AR 被压缩，标记；连续涨跌停 CAR 标"流动性受限，幅度被低估"。

### 4.5 数据清洗（默认初值；结构固定）

剔除事件日前上市不足 250 交易日新股（CandidateGen 即用此阈值）；thin trading（日均换手<0.1% **或** 零成交日>10%）→ 低置信；估计窗有效交易日<60 → 低置信；停牌日剔除（volume=0），事件窗缺失>20% → 低置信【2026-10-19 回写：事件窗内停牌实际未打 `low_confidence`，仅剔除；见文末】；ST 按事件期当时名称，估计窗内戴/摘帽 → 低置信；估计窗与已知重大事件重叠 → 标注污染；低置信标的保留进报告但不参与强弱排序；日线取不到 → 记 skip_log。

### 4.6 EventEpisode（渐变事件）

`Event.episodes[]` 每子事件含自己的 reaction_date；**单事件时 episodes 恰为 1 元素、episode_id 必填**；ImpactMeasurement 带 `episode_id`（标的×子事件二维）。**每子事件用各自估计窗 `[t0−120, t0−21]` 估 β（不锚定首子事件）**；某子事件估计窗与前序子事件事件窗重叠 → 标 `beta_unreliable`/low_confidence。MVP 取 `|CAR[0,+20]|` 最大子事件为代表，方向冲突分窗报告；不承诺子事件间 β 一致性。完整聚合留后续。

### 4.7 基准选择（spike 调整）

**主基准 = 沪深300价格指数（sh000300，sina 可得）**——全收益 H00300.CSI 不可得（spike 确认）。**报告固定披露"基准为价格指数，β 口径含分红偏差（高股息行业偏甚）"**。申万行业指数（仅价格）作行业暴露诊断。**小盘/题材事件披露**：池内标的市值中位数低于沪深300 成分股市值 20 分位时，报告披露"基准不匹配，CAR 可能含 size 暴露"。CLI `--benchmark`：`hs300p`（默认）/ 自定义。

---

## 5. CLI 接口

```bash
eventreplay replay --desc "双减政策落地" --date 2021-07-24 \
    [--reaction-date 2021-07-26] [--episode-dates 2021-07-24,2021-08-30] [--benchmark hs300p]
eventreplay replay --from-file events/double-reduction.yaml
# 产物：reports/<event-id>.md + 结构化数据入 SQLite + skip_log
```

> 【2026-10-19 回写：`--episode-dates` / `--from-file` 未实现；replay 实际入口为 `--desc` + `--date`（多 episode 仅 schema + 度量支持，无 CLI 入口），见文末】

---

## 6. 种子事件集（5 个验收夹具）

| # | 事件 | announcement_date | reaction_date(t0) | 类型 | 预期方向（按窗口区分） |
|---|---|---|---|---|---|
| 1 | 中美贸易战开端 | 2018-03-22（美东凌晨） | **2018-03-23** | 地缘/政策 | 短窗 [0,+5] 大概率全负（大盘暴跌 −3.39%）；中长窗 [0,+60] 国产替代正超额；稀土显著需到 2019-05 后 |
| 2 | 新冠冲击 | 2020-01-20 前后 | **2020-02-03**（节后首日） | 疫情 | [0,+20]/[0,+60] 医药、在线经济正超额；航空/旅游/线下消费负 |
| 3 | 双减政策 | 2021-07-24（周六） | **2021-07-26** | 监管 | K12 教培（豆神教育/学大教育/昂立教育，事件期 2021-07 尚未 ST）强负；素质/职业教育待证 |
| 4 | ChatGPT 引爆 AI | 2023-01（节前已异动） | **2023-01-30**（节后首日，AI 概念启动） | 技术突破 | [0,+20]/[0,+60] 算力（寒武纪、中科曙光）、光模块（中际旭创/新易盛/天孚通信）、AI 应用正超额；**受损方向无明确预期（单边受益事件）** |
| 5 | 新国九条 | 2024-04-12（盘后） | **2024-04-15** | 政策 | 红利/高股息（水电/煤炭/银行龙头）正超额；绩差小盘（主要 4-15/4-16）负 |

> 代码为代表性示例（海光信息因 2022-08-12 上市、到 2023-01-30 不足 250 交易日会被 §4.5 剔除，故用中科曙光——注：产业链节点不同，spike 后据实选定同节点替代）；§4.0 spike 后固化为 `expected_beneficiary_set`/`expected_victim_set`（每方向 ≥5 只，可为空并声明）= 验收基线。
>
> 【2026-10-19 回写：5 个种子验收夹具文件 `tests/fixtures/seeds/*.yaml` 未落地；验收脚本在 `spikes/accept_5seeds.py`（spikes 共 39 个脚本 / 3,540 行），见文末】

**验收标准（可机器判定）**：受益/受损池分别计 N，两池均须 `ceil(N×0.5)` 命中且 direction 一致（合取）；**某方向 `expected_*_set` 为空（单边事件，如 #4 受损池）→ 该方向不参与合取，降为另一方向 ceil(N×0.5) 命中**；命中 = 标的出现在受益/受损表且 direction 与预期一致（低置信/direction_conflict 标的进表、计入命中，仅分级列标"未分级/方向冲突"）；N=5 时 ceil=3。短窗系统性暴跌全负为合理，主断言落 [0,+20]/[0,+60] 方向。

---

## 7. 报告模板（markdown）

```
# <事件> 复盘报告
## 1. 事件摘要（announcement_date / reaction_date / 公告时点 / 类型 / 冲击维度 / 影响方向）
## 2. 产业链传导（mermaid 有向图，节点含 sw_industry(一级) + 文字 + 行业粒度；>50% 节点低置信时头部标注）
## 3. 受益/受损标的表（代码/名称/所属子事件/CAR[0,+1]/[0,+5]/[0,+20]/[0,+60]/持续性(主=长短窗CAR比)/强弱分级/涨跌停天数/置信标记/direction_conflict/逻辑）
## 4. 逻辑链叙述（来自 TransmissionChainSynthesis）
## 5. 局限与数据说明（估计窗/基准=价格指数+β分红偏差+size暴露披露/清洗/涨跌停时点规则/事件窗大盘走势/组合KP统计量(GRANK+Adjusted BMP,含分歧披露)/skip_log/行业暴露诊断/产业链置信度/行业粒度=一级）
```

> 单标的不列"CAR t 值"列；显著性由经济阈值 + 组合统计量（局限区）表达。

---

## 8. 编排与可复现

轻量 Pipeline：节点图 + 每节点缓存 + 断点续跑。【2026-10-19 回写：节点级缓存已实现且支持跨进程读写（`pipeline/node.py:84-145`）；重跑同一事件可跳过已完成节点（等效续跑），但无独立 resume 命令入口，见文末】
- **缓存键**：`node_id + node_code_hash + inputs_hash + prompt_hash + model_alias + cli_version + cli_flags_hash`
- **缓存有效边界**：量化节点（ImpactMeasurement）与首个推理节点缓存有效；推理→推理下游因 LLM 非确定性 cache 命中率被压制（断点续跑仍按 node_id 可用）
- **可复现 = 可追溯（非确定性）**：CLI 不暴露 `--temperature`；记录 `pipeline_version`/`prompt_hash`/`model_alias`/`cli_version`；不承诺数值可复现。规律库（Phase2）固定 alias + cli_version

---

## 9. 测试策略

| 层 | 方法 |
|---|---|
| 量化数值（解析解） | 合成数据断言 CAR/方差/KP(2010) Adjusted BMP 等于 closed-form（紧容差） |
| 量化数值（秩检验） | KP(2011) GRARK：断言实现等于参考秩变换 + 蒙特卡洛 size 检验 + GRANK vs Adjusted BMP 一致性回归 |
| 量化清洗（脏数据夹具） | 停牌缺口、上市不足250日、估计窗<60日、连续涨跌停、低换手、跨涨跌停时点（2018/2020/2023）；断言清洗/低置信/涨跌停标记 + skip_log |
| 推理节点契约 | mock CLI 验证 Pydantic + §3.2 每节点降级分支；真实 CLI 冒烟（--bare 必须）实测 `--json-schema` 失败时 `.structured_output` 缺失 |
| 数据护栏 | 幻觉代码 vs 历史曾用名真标的（双源交叉+异常捕获）；sw_industry 语义夹具（错配→低置信；正确不同表述→通过） |
| 端到端冒烟 | 5 种子事件各跑一遍，受益/受损池分别 ceil(N×0.5) 命中（空池跳过）+ direction 一致 |
| 数据可用性 | 已由 spike 验证（§4.0 / [spike-results](./2026-06-30-spike-results.md)） |

---

## 10. 目录结构

```
src/eventreplay/
  adapters/        # quote_adapter.py(sina), stock_master.py
  schema/          # event.py, measurement.py, attribution.py, chain.py, replay.py
  llm/             # reasoner.py + claude_code_provider.py
  pipeline/        # nodes/*.py, orchestrator.py
  measurement/     # event_study.py（市场模型 + 手写 GRANK / Adjusted BMP + 经济显著性）
  cli.py
tests/
reports/
spikes/            # spike 脚本（已跑）
docs/superpowers/specs/
```

---

## 11. 依赖与部署

- 行情：`akshare`（sina 主源；eastmoney/baostock 本环境不可达，不作 fallback）；度量：`pandas`/`numpy`/`scipy`/`statsmodels`；schema：`pydantic`；存储：`sqlite3`；CLI：`typer`；重试：`tenacity`；测试：`pytest`。GRANK 手写（不依赖 event-study-toolkit）
- **鉴权部署**：GLM 网关用 `ANTHROPIC_AUTH_TOKEN`+`ANTHROPIC_BASE_URL`（3P provider 凭证，实跑可用）；直连 Anthropic 用 `ANTHROPIC_API_KEY`/`apiKeyHelper`。`--bare` 必须用于子进程（否则 .mcp.json 卡死）
- LLM 经 Claude Code CLI（`--json-schema`，`--bare` 必须，`--model` alias）。向量库 `sqlite-vec` 留待 Phase 2

---

## 12. 下一步

spec v2.5 已由 spike 实测奠基 → writing-plans 生成 Phase 1 实施计划。实施顺序：基座（sina QuoteAdapter + StockMaster）→ 量化节点（合成测试先行，含手写 GRANK）→ 推理节点契约（--bare 必须）→ 5 种子事件端到端冒烟。

---

## 13. 变更日志

**v2–v2.4**：见前（CLI 用法/聚集调整/事件日标准化/候选底池检索/BHAR/GRANK 升默认/两段式/EventEpisode schema/语义护栏/验收 N；以及 v2.4 据实修正 `--bare`/GRANK/改名异常 + 逻辑闭合）。

**v2.5（spike 实跑驱动）**：据 [spike-results](./2026-06-30-spike-results.md) 用 ground truth 替换所有数据/CLI 推测项：
- **数据主源换 sina**：个股后复权 `stock_zh_a_daily`（eastmoney `stock_zh_a_hist` 不可达）；指数 `stock_zh_index_daily`；市值从 outstanding_share×close 推导
- **沪深300全收益 H00300.CSI 不可得** → 价格指数 sh000300 作主基准 + 报告固定披露 β 分红偏差（不再是 fallback，是实际路径）
- **申万成分股仅一级可用**（二/三级 KeyError）→ CandidateGen 用一级底池，报告披露粒度
- **baostock 移出 fallback**（本环境 TCP 不可达）
- **`--bare` 必须**（不带则 .mcp.json forge_extension 卡死，实跑确认）+ GLM 网关 AUTH_TOKEN 鉴权实跑可用
- **GRANK 手写**（event-study-toolkit 不暴露可用 GRANK，实跑确认；v2.4 的"候选库"修正）
- **`stock_info_sz_change_name('简称变更')`、`stock_info_change_name` 假代码 KeyError** 实跑确认正确
- §4.0 从"待验证 gate"转为"已验证结论 + 残余低风险项"

**v3.0（实现期迭代，真实事件复盘驱动）**：经印巴空战/中日管制两轮真实复盘验证后的改进：
- **+90 事件窗口**（§4.1/§4.4）：`CAR[0,+90]` 新增；捕捉 late-bloomer（如中船特气 +60=+23% → +90=+106%，原 +60 严重低估持续性）
- **tushare 申万三级权威成分股源**（替代 v2.5"仅一级可用"）：`pro.index_classify(L3)` + `pro.index_member` → 346 行业 + 成分股；`refresh-sw` 预拉到本地 pickle（28KB），后续零网络
- **AttributionFiltering car_best**（§4.3）：显著性从"仅看 +20"→"+20/+60/+90 任一窗口 max|CAR|"；修 late-bloomer 漏入池（受益池 16→42）
- **CandidateGen 三路合并**（§3）：① LLM 提议 + ② tushare 权威成分 + ③ curated themes.yaml 兜底；候选封顶 40
- **ReportAssembly Python 模板兜底**（§3.2）：LLM 超时/失败 → `render_report()` 确定性生成（秒级，数据完整，无归纳叙述）
- **`.env` 配置**：`ANTHROPIC_AUTH_TOKEN`/`TUSHARE_TOKEN`/`EVENTREPLAY_*` 默认值；python-dotenv 自动加载
- **依赖新增**：`python-dotenv`、`pyyaml`、`tushare`

---

## 实现期状态回写（2026-10-19）

> 本节为 Phase 1 实现完成后的回写；上文为 2026-06-30 设计（含 v2.5/v3.0 变更日志）与实现期迭代记录，未改写。

- **落地情况**：7 节点流水线 + 影响度量引擎 + markdown 报告模板全部落地。代码规模：`src/eventreplay` 61 个 `.py` / 8,002 行；`tests` 35 个测试文件 / 7,159 行；`spikes` 39 个脚本 / 3,540 行。本地库 `data/eventreplay.db`（11.8MB，9 表）已累计：`events` 61、`chains` 61、`attribution_results` 61、`measurements` 2,746、`patterns` 58、`pipeline_runs` 531（跑前 525）、`seen_events` 107（跑前 83）、`skip_log` 673、`replays` 0。`reports/` 收尾时点 96 份 `.md` = **64 份复盘报告** + 22 份前瞻（`reports/` 顶层）+ 10 份监测（`monitoring/`），按报告首行标题判定，覆盖 2016–2026 年事件；2026-10-19 monitor 实跑后为 100 份（64 + 24 + 12）。
- **CLI 实际 8 子命令**：`replay`、`extract-pattern`、`find-similar`、`refresh-sw`、`forward`、`mine-events`、`batch-replay`、`monitor`。
- **未落地/偏离（已知）**：
  - `--episode-dates` / `--from-file` 未实现；replay 入口为单事件 `--desc` + `--date`。多 episode 仅 schema + 度量支持，无 CLI 入口；
  - Phase 1 计划承诺的 5 个种子验收夹具 `tests/fixtures/seeds/*.yaml` 未落地；验收脚本为 `spikes/accept_5seeds.py`；
  - GRANK / Adjusted BMP 已在 `measurement/significance.py` 实现，但 `AttributionFiltering`（`attribution.py`）仍用 scipy t 检验——`PoolStat.adj_bmp_*` 恒为空、报告注“简化 t 检验”，§4.3 组合推断尚未接入归因；
  - 跨进程续跑（§8）：节点级 SQLite 缓存读写已支持（`pipeline/node.py:84-145`，含类型重建），重跑同一事件可跳过已完成节点（等效续跑）；但无独立 resume 命令入口，且 `replays` 表当前为空使中断状态不可追溯；
  - 事件窗内停牌未标 `low_confidence`（仅剔除）；
  - 研究级方法（安慰剂检验、FF3/FF5、Dimson β、BHAR 显著性、AR(1) 半衰期等，§1.3 非目标）保持未做。
- **口径更新**：成分股召回已由 **tushare 申万三级**（`refresh-sw` 预拉本地 + `ThemeIndex` curated 兜底 + LLM 提议 resolve 反查）实现；文中“申万成分股仅一级可用”为 v2.5 旧结论，已被 v3.0 取代。
- **测试**：日常离线测试 `pytest --ignore=tests/test_real_smoke.py` → **365 passed / 8 skipped / 0 failed**（约 35s，纯离线；2026-10-19 收尾时 342，2026-10-20 新增 23 个回归测试）；`tests/test_real_smoke.py` 为触网 + 真实 LLM 冒烟，不纳入日常验证（**2026-10-19 实跑通过：1 passed / 494.47s**，7/7 节点 done、31 条 measurements、报告 11,299 字，真实 sonnet 网关）。
- **环境**：Python 3.10.10；`pyproject.toml` `requires-python >= 3.10`。
