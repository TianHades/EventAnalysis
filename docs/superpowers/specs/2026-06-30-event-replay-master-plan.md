# EventAnalysis 总计划：重大事件复盘与前瞻映射系统

- **状态**：已实现（五阶段全部落地，2026-10-19 状态回写；偏差见 §10）
- **日期**：2026-06-30
- **文档层级**：程序级总计划（program-level）。各阶段各自再出独立 spec。

---

## 0. 决策摘要（关键约束一览）

| 维度 | 决策 |
|---|---|
| 核心定位 | 复盘 + 规律 + 前瞻，完整闭环 |
| 分析引擎 | LLM-Agent 驱动（大模型负责拆解/推理/归纳，量化数据作证据） |
| 编排架构 | **方案 A**：Python 主编排 + Claude Code CLI 作为推理节点 + 显式流水线 |
| 标的市场 | A 股先行，数据层与标的解析预留多市场扩展 |
| 事件范围 | **放眼全球**（国内政策、中美/地缘、商品、贸易、疫情、技术突破、海外宏观等） |
| 数据来源 | 最佳实践：免费为主 + 轻量付费 + 抓取 + LLM 知识；数据适配器抽象可替换 |
| 前瞻触发 | 全自动监测全球新闻 → 识别重大事件 → 推送 |
| 技术栈 | Python 3.11+【2026-10-19 回写：实际为 Python 3.10.10，`pyproject.toml` 要求 `>=3.10`，见 §10 ③】；LLM 经 **Claude Code CLI（headless）** 调用，基座模型可灵活调整 |

---

## 1. 愿景与范围

### 1.1 愿景

把分析师的复盘思维——"重大事件 → 产业链传导 → 受益/受损标的 → 影响幅度与持续性 → 逻辑链"——沉淀为**可复现、可累积、可类比检索**的系统。既能对历史事件做深度复盘并提炼规律，也能对新事件做前瞻映射。

### 1.2 范围边界

- **标的池**：A 股先行；数据层与标的解析预留多市场接口
- **事件域**：全球事件（国内政策、中美/地缘、商品、贸易、疫情、技术突破、海外宏观如联储议息等）
- **产物**：复盘报告 + 事件规律库 + 前瞻映射 +（后期）全自动监测推送

### 1.3 非目标（YAGNI）

- 不做实时交易执行
- 不做组合优化/回测策略
- 不做分钟级量化策略

---

## 2. 核心概念与全局数据模型

统一定义跨阶段复用的实体（Phase 1 落地 schema）。**Phase 1 在此基础上扩展**：新增 `AttributionResult` 实体、`Event.episodes[]`/`EventEpisode`、`ImpactMeasurement` 细化字段（`episode_id`/`low_confidence`/`chain_node_id`/`direction`/`tier`/`rationale`/涨跌停聚合/持续性子字段）——**全局数据模型以 Phase 1 §2 schema 为准**，本表待 Phase 1 落地后回写。【2026-10-19 回写：已完成——以 Phase 1 §2 schema 为准，`AttributionResult`/`EventEpisode`/`ImpactMeasurement` 细化字段均已落地，见 §10 ②】

| 实体 | 关键字段 | 说明 |
|---|---|---|
| **Event** | id, 时间(日/可带时点), 类型(枚举), 摘要, 来源URL, 关键实体, 地理, 行业标签, 冲击维度(供给/需求/政策/流动性/情绪), 影响方向, 是否"事件集" | 全球事件统一建模；渐变型政策用 EventEpisode 聚合多个子事件 |
| **IndustryNode** | 节点名, 层级(上/中/下游), 关系(替代/互补/客户/供应商), 受冲击方向 | 产业链节点 |
| **Stock** | 代码, 名称, 市场, 所属行业/概念, 关联产业节点 | 标的池 |
| **ImpactMeasurement** | 标的, 事件id, 估计窗, 事件窗([+1]/[+5]/[+20]/[+60]…), CAR, t值, 峰值AR, 涨停/跌停次数, 异常成交量(AAV), 持续性指标 | 量化证据，由 Python 计算 |
| **TransmissionChain** | 事件id, 有向图(节点+边+方向+强度), 受益/受损标的分组 | 逻辑链（图结构） |
| **Replay** | 事件id, 流水线版本, prompt版本, 模型版本, 各节点产物(JSON), 最终报告 | 一次完整复盘（可复现） |
| **Pattern** | 事件签名(类型+维度+行业), 传导模板, 典型受益/受损标的及幅度/持续性区间, embedding | 从多次 Replay 提炼的规律 |

---

## 3. 整体分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  应用层   复盘CLI/报告  ·  规律库检索  ·  前瞻映射  ·  推送   │
├─────────────────────────────────────────────────────────────┤
│  编排层   Python 轻量 Pipeline (节点图·缓存·断点续跑·版本记录)│
│           ┌──────────────┐         ┌──────────────────────┐  │
│           │ 量化节点(Python)│       │ 推理节点(Claude CLI) │  │
│           │ CAR/持续性/统计 │       │ 拆解/产业链/归纳     │  │
│           └──────────────┘         └──────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  知识层   Replay库 · Pattern库(向量+结构化) · 产业链图谱      │
├─────────────────────────────────────────────────────────────┤
│  数据层   QuoteAdapter │ NewsAdapter │ ChainAdapter (可替换) │
│           akshare/tushare │ forge MCP+RSS │ LLM知识+人工      │
└─────────────────────────────────────────────────────────────┘
```

**架构要点**：Python 做确定性工作（行情/度量/存储/调度/推送）；Claude Code CLI 做推理工作（拆解/产业链/归纳）。推理节点是无状态、可重放、可单独测试的"推理原子服务"，节点间用结构化 JSON 传递。

---

## 4. 影响度量方法（"幅度与持续性"的核心）

采用**事件研究法（Event Study）**，由 Python 量化节点计算，作为证据喂给推理节点。

- **事件日 t=0**：明确公告日；渐变事件取首次显著反应日或设事件窗
- **估计窗 [t−120, t−21]**：拟合市场模型 `R_i = α + β·R_m + ε`
  - 基准：默认沪深300全收益（H00300.CSI）；申万行业指数（akshare 仅价格指数）仅作行业暴露诊断；可切换。详见 Phase 1 §4.7【2026-10-19 回写：全收益不可得，实际主基准为沪深300价格指数 sh000300（Phase 1 §4.7），见 §10 ③】
- **多事件窗**：[0,+1]（即时）、[0,+5]（短）、[0,+20]（中）、[0,+60]（长），对应"持续性"的多周期视角
- **幅度指标**：各窗口 CAR（累计异常收益）、峰值 AR、涨停/跌停次数、最大回撤
- **持续性指标**：长/短窗口 CAR 比（主）、显著 AR 持续天数、AAV 持续天数；A 股修正（涨跌停时点规则、组合显著性 KP GRANK 默认 + Adjusted BMP 对照、事件日标准化）与工程细节见 Phase 1 spec
- **显著性**：AR/CAR 的 t 统计量，过滤噪声
- **板块层面**：同步算行业指数/概念指数的 CAR，定位"板块效应 vs 个股 alpha"

> 度量规格的细节（窗口取舍、停牌/ST 处理、新股剔除、基准选择、t 值计算公式）在 Phase 1 spec 中确定。

---

## 5. LLM 推理流水线（复盘节点图）

复盘 = 一条显式 pipeline，节点交替为 Python（量化）与 Claude CLI（推理）：

```
[EventIngest*] → [IndustryChainReasoning*] → [CandidateGeneration*]
   → [ImpactMeasurement#] → [AttributionFiltering*] → [TransmissionChainSynthesis*]
   → [ReportAssembly*]      (* = Claude 推理节点,  # = Python 量化节点)
```

| 节点 | 类型 | 职责 |
|---|---|---|
| EventIngest | 推理 | 把事件原文结构化为 Event 实体 |
| IndustryChainReasoning | 推理 | 推理受冲击产业链节点与方向 |
| CandidateGeneration | 推理 | 生成候选受益/受损标的 + 初步逻辑 |
| ImpactMeasurement | 量化 | 对候选池算 CAR / 持续性 / 显著性 |
| AttributionFiltering | 推理 | 用量化证据回检，剔除无反应标的，标注强弱等级 |
| TransmissionChainSynthesis | 推理 | 归纳完整逻辑链（图结构） |
| ReportAssembly | 推理 | 产出复盘报告 |

- 每个推理节点：Python 以 `claude -p --output-format json --json-schema '<schema>' --bare --disable-slash-commands --permission-mode dontAsk --model '<id>'` 调用；读 `.structured_output` + Pydantic 校验 + 修复式重试 + 降级（**`--output-format json` 控制输出封装/`.structured_output` 容器，`--json-schema` 控制 schema 强制力，二者职责不同均需传**；`--bare` 不跳 skills 故加 `--disable-slash-commands`；完整 flags/鉴权见 Phase 1 §2）
- 节点间传递结构化 JSON
- 流水线记录版本（pipeline / prompt / model）保证可复现
- 每节点的 prompt 契约、JSON schema、降级策略在 Phase 1 spec 中确定

---

## 6. 分阶段路线图

| 阶段 | 交付物 | 依赖 | 闭环价值 |
|---|---|---|---|
| **Phase 0 基座** | 数据适配器接口 + A股行情接入 + 全局 schema + Claude CLI 调用封装 | — | 可拉行情、可调 LLM |
| **Phase 1 复盘 MVP** | 复盘 pipeline 7 节点 + 度量引擎 + 复盘报告 | P0 | **手动复盘跑通，独立可用** |
| **Phase 2 规律库** | Replay → Pattern 提炼 + 向量+结构化检索 | P1 | "找相似历史事件" |
| **Phase 3 前瞻映射** | 新事件 → 检索规律 → 映射标的+逻辑链（复用 P1 pipeline） | P2 | 前瞻闭环 |
| **Phase 4 监测推送** | 全球新闻采集 → 事件抽取打分 → 自动触发 P3 → 推送 | P3 | 全自动闭环 |
| **Phase 5 多市场** | 港股/美股行情与标的池 | P1 | 跨市场复盘 |

> 【2026-10-19 回写：五阶段均已实现（Phase 0 按设计并入 Phase 1），实际交付与规模数据见 §10 ①。】
>
> **关于 Phase 0**：Phase 0 的必需基座（行情适配器 / 标的主数据 / 全局 schema / Claude CLI 封装 / 存储）**并入 Phase 1 一起实现，不单独成阶段交付**。原 Phase 0 设想中的新闻采集适配器（NewsAdapter）推迟到 Phase 4，独立产业链图谱库作为后期增强——避免过早建设当前用不上的东西（YAGNI）。

**建设原则**：每阶段独立可用、为下一阶段铺路；每阶段各自走 spec → 计划 → 实现循环。

---

## 7. 技术栈选型

| 层 | 选型 | 说明 |
|---|---|---|
| 语言/运行时 | Python 3.11+ | 主编排 |
| LLM 引擎 | **Claude Code CLI（headless）** | `claude -p --json-schema` + Pydantic 校验；基座模型可灵活调整（记录 spike 确认的最具体 model id + cli_version；`--model` 接受形式与 `--bare` 鉴权随部署而异，Phase 1 §4.0 spike gate）；CLI 无 `--temperature` |
| 行情 | akshare（sina 主源；eastmoney/baostock 在开发环境不可达，见 [spike-results](./2026-06-30-spike-results.md)） | 走 QuoteAdapter 接口 |
| 新闻/事件 | forge_extension MCP + 财经 RSS/API + LLM 抽取 | 走 NewsAdapter |
| 产业链 | LLM 知识 + 人工维护入库 | 走 ChainAdapter |
| 度量 | pandas / numpy / scipy / statsmodels | CAR、市场模型、t 值 |
| 存储 | SQLite 或 DuckDB（结构化）+ 向量库（Chroma / sqlite-vec） | Replay / Pattern / 事件 |
| 编排 | 自写轻量 pipeline（节点图 + 缓存 + 续跑） | 后续可升 Prefect |
| 调度/推送 | APScheduler + requests（企业微信/钉钉/邮件/webhook） | Phase 4 |
| 测试 | pytest | 量化节点数值正确性 + 推理节点契约测试 |

> 【2026-10-19 回写：实际技术栈见 §10 ③——Python 3.10.10；新闻源为 akshare+cctv+cls+jin10+jiuyan 融合；向量检索为 SQLite BLOB + 暴力 cosine（未用 Chroma/sqlite-vec）；embedding 为 auto 双后端（在线 2048 / 本地 512）。】

---

## 8. 非功能约束与风险

### 8.1 非功能约束

- **可替换性**：数据源、LLM provider、存储均接口化（模型可灵活调整 → 走 provider 抽象）
- **成本控制**：推理节点可缓存、按需调用；量化节点零 LLM 成本
- **可复现**：每次 Replay 记录 pipeline / prompt / model 版本
- **可复现 = 可追溯（非确定性）**：CLI 不暴露 temperature 且 temp=0 亦非确定；`--model` 接受形式随部署而异（Phase 1 §4.0 spike gate），记录 `pipeline_version / prompt_hash / model_id(spike确认) / cli_version`，不承诺数值可复现，靠版本记录 + 缓存命中
- **合规**：尊重数据源使用条款；输出标注"研究用途，非投资建议"

### 8.2 关键风险与缓解

| 风险 | 缓解 |
|---|---|
| 新闻抓取稳定性（MCP/网站变更） | 降级到多源 + 重试 + 缓存；抓取层接口化 |
| 事件日界定（尤其渐变政策） | 支持 EventEpisode 多子事件；取首次显著反应日 |
| LLM 幻觉导致产业链/标的误判 | 用量化证据回检兜底（AttributionFiltering 节点） |
| 模型版本漂移导致结果不可复现 | 每次 Replay 记录三版本（pipeline/prompt/model） |
| 数据源配额/频控 | 适配器层限流 + 本地缓存 |
| CLI 推理节点端到端成功率衰减（7 串联节点 ≈ p^7） | `--json-schema` + Pydantic + 修复式重试 + 明确降级语义；节点级缓存命中 |
| akshare 历史数据可得性 / 退市停牌行为不一致 | 动工前数据可用性 spike；按 volume 判停牌；baostock fallback【2026-10-19 回写：baostock 本环境不可达，未作 fallback，见 §10 ④】 |

---

## 9. 下一步

- 本文档为程序级总计划
- 下一步：**Phase 1（复盘 MVP）独立 spec**，细化：
  - 各推理节点的 prompt 契约与 JSON schema
  - 影响度量的工程规格（窗口、基准、停牌/ST/新股处理、t 值公式）
  - 复盘报告模板
  - pipeline 编排与缓存/续跑机制
  - 测试策略（量化节点数值测试 + 推理节点契约测试）
- Phase 1 spec 完成后，调用 writing-plans skill 生成实施计划

---

## 10. 状态回写（2026-10-19）

> 本节为五阶段实现完成后的全局回写；§0–§9 为 2026-06-30 的设计历史，未逐一改写。

### ① 五阶段实现事实

| 阶段 | 状态 | 实现事实（2026-10-19） |
|---|---|---|
| Phase 0 基座（并入 P1） | ✅ | sina QuoteAdapter / StockMaster / 全局 schema / Claude CLI 封装 / SQLite 存储均已落地 |
| Phase 1 复盘 MVP | ✅ | 7 节点流水线 + 度量引擎（含 +90 窗口；GRANK/Adjusted BMP 有实现）+ 报告模板；已产出 64 份复盘报告、61 个 events |
| Phase 2 规律库 | ✅ | Pattern schema + `extract-pattern` / `find-similar`；58 个 Pattern 入库（全部含五维定性 + embedding） |
| Phase 3 前瞻映射 | ✅ | 三层降级 + 五维事前定性（§1.5）+ Forward 报告；已产出 24 份前瞻类报告（8 份 `fwd_*` 评估 + 16 份按日期命名的预判，其中 2 份由 2026-10-19 monitor 实跑触发；最新 `reports/fwd_20261001_repo_outright.md`） |
| Phase 4 监测/建库 | ✅ | `mine-events` / `batch-replay` / `monitor` 全链路；监测报告 12 份（跑前 10）；事件打分实际 5 维；推送含飞书/企微/钉钉/文件；`monitor --once` 支持单轮执行 |
| Phase 5 港股 | ✅ | 港股行情/主数据/HSI 基准/`--market 港股` 已实现并有单测（也接受 hk/hkex 等别名，未知值报错）；已产出 `reports/hk_tencent_dump.md` |

- 代码与测试规模：`src/eventreplay` 61 个 `.py` / 8,002 行；`tests` 35 个测试文件 / 7,159 行；`spikes` 39 个脚本 / 3,540 行。
- 离线测试：`pytest --ignore=tests/test_real_smoke.py` → **365 passed / 8 skipped / 0 failed**（约 35s，纯离线；2026-10-19 收尾时 342，2026-10-20 新增 23 个回归测试）。
- 数据库 `data/eventreplay.db`（11.8MB，9 表）：`events` 61、`chains` 61、`attribution_results` 61、`measurements` 2,746、`patterns` 58、`pipeline_runs` 531（跑前 525）、`seen_events` 107（跑前 83）、`skip_log` 673、`replays` 0。
- 产物 `reports/` 100 份 `.md` = 64 份复盘 + 24 份前瞻（`reports/` 顶层）+ 12 份监测（`monitoring/`），按报告首行标题判定，覆盖 2016–2026；收尾时点为 96 = 64 + 22 + 10，另 4 份为 2026-10-19 monitor 实跑新增；最新前瞻 `reports/fwd_20261001_repo_outright.md`（2026-10-01）。
- CLI 实际 8 子命令：`replay`、`extract-pattern`、`find-similar`、`refresh-sw`、`forward`、`mine-events`、`batch-replay`、`monitor`。
- Pattern 年份分布（58 个）：2016:10 / 2017:7 / 2018:3 / 2019:4 / 2020:4 / 2021:6 / 2022:7 / 2023:8 / 2024:4 / 2025:5；61 个 events 中 3 个无 Pattern（`china_japan_dualuse_2026`、`hk_tencent_dump`、`india_pakistan_2025`）。

### ② 全局数据模型回写（补 §2 回写触发点）

§2 所述“全局数据模型以 Phase 1 §2 schema 为准，待 Phase 1 落地后回写”已完成：`AttributionResult`（受益/受损池、`pool_stats`、`tier_definitions` / `industry_granularity`）、`EventEpisode`（`Event.episodes[]`，单事件恰 1 元素）、`ImpactMeasurement` 细化字段（`episode_id` / `low_confidence` / `chain_node_id` / `direction` / `tier` / `rationale` / 涨跌停聚合 / 持续性子字段）均已落地。Pattern 实体另增五维定性字段（见 §10 ① 与 Phase 2 spec 文末回写）。

### ③ 技术栈实际（对 §7 / §0 表的回写）

- **语言/运行时**：Python **3.10.10**；`pyproject.toml` `requires-python >= 3.10`（§7 写“3.11+”为设计期口径，已过时）。
- **LLM**：经 **claude CLI**（`--bare --json-schema --output-format json` + Pydantic 校验 + 修复式重试），与设计一致。
- **Embedding**：`EMBEDDING_BACKEND` 默认 **auto**——有 `ZHIPU_API_KEY` 且可达 → 智谱 `embedding-3`（2048 维）；否则本地 `bge-small-zh-v1.5`（512 维）；首次选定后锁定不切换（`cache/embedder_backend.txt`）。非设计期设想的固定网关口径。
- **新闻源（实际）**：**akshare + cctv + cls（财联社）+ jin10（金十）+ jiuyan（韭研公社）融合**；原设计“forge_extension MCP + 财经 RSS/API”未按原样落地。
- **存储/检索**：SQLite（`data/eventreplay.db`）+ BLOB 向量 + 暴力 cosine；未使用 Chroma / `sqlite-vec`。
- **调度/推送**：**`while True + sleep` 单进程循环**（`pipeline/monitoring_pusher.py:22-41`；未使用 APScheduler，`pyproject.toml` 未引入该依赖）+ webhook（企微/钉钉/飞书）+ 本地文件。

### ④ 已知未完成 / 简化（不阻塞当前使用）

- **GRANK/Adjusted BMP 池级统计**：`measurement/significance.py` 已实现，但归因（`attribution.py`）仍用 scipy t 检验，`PoolStat.adj_bmp_*` 恒空、报告注“简化 t 检验”；
- **跨进程续跑**：节点级 SQLite 缓存读写在后续迭代已补齐（`pipeline/node.py:84-145`，按 `output_type` 重建，`tests/test_fixes_regression.py` 覆盖），重跑同一事件可跳过已完成节点（等效续跑）；但无独立 resume 命令入口；
- **多 episode 入口**：仅 schema + 度量支持，CLI 无 `--episode-dates` / `--from-file`；
- **研究级方法**：安慰剂检验、FF3/FF5、Dimson β、BHAR 显著性等未做（原非目标）；
- **市场与实时性**：美股、港股通南向、港股 GEM、港股衍生品未做；监测仍定时轮询（非实时 tick）；
- **其他**：`calibrate_qualitative` 有实现有测试但未接入 forward 主链路；Phase 1 计划的 `tests/fixtures/seeds/*.yaml` 5 个种子夹具未落地（验收脚本在 `spikes/accept_5seeds.py`）。
