# Phase 1 实施计划：复盘 MVP

- **状态**：**M0–M5 全部实现完成**（2026-06-30）。65 单测 passed + 1 skipped；组件级真实端到端验证通过（真实 claude 6 推理节点 + 真实 sina 行情 + 真实 StockMaster）。**2026-10-19 回写：现状 342 passed/8 skipped（收尾新增 4 个回归测试）；实现期偏离见 §实现期偏离（含 5 种子夹具未落地、--episode-dates/--from-file 未实现、报告命名与计划不符）。**
- **日期**：2026-06-30
- **依据 spec**：[phase1-replay-mvp.md v2.5](../specs/2026-06-30-phase1-replay-mvp.md) · [spike-results](../specs/2026-06-30-spike-results.md) · [总计划](../specs/2026-06-30-event-replay-master-plan.md)
- **方法**：TDD（量化节点测试先行）+ 增量集成 + 每里程碑可验证
- **完成定义**：5 个种子事件端到端跑通，报告生成 + 验收基线命中（§M5）

> 数据/CLI 事实均经 spike 实跑确认（见 spike-results）。本计划只描述"怎么做"，不再争论"对不对"。

---

## 里程碑总览

| 里程碑 | 内容 | 依赖 |
|---|---|---|
| **M0 基座** | 脚手架 + schema + 存储 + sina QuoteAdapter + StockMaster + Claude CLI 封装 | — |
| **M1 度量引擎** | 市场模型 + CAR + 显著性（手写 GRANK/Adjusted BMP）+ 涨跌停 + 清洗 | M0 |
| **M2 编排** | 节点图 + 缓存 + 断点续跑 + 版本记录 | M0 |
| **M3 推理节点** | 7 节点的 6 个推理节点（prompt 契约 + Pydantic schema + 降级） | M0、M1（ImpactMeasurement 是量化节点，M1 产物） |
| **M4 CLI + 集成** | typer CLI + 7 节点端到端串联 | M2、M3 |
| **M5 验收** | 5 种子事件 fixture + 端到端冒烟 + 报告抽查 | M4 |

---

## M0 · 基座（Phase 0 并入）

### M0.1 项目脚手架
- `pyproject.toml`（Python 3.10+）：`akshare`、`pandas/numpy/scipy/statsmodels`、`pydantic`、`typer`、`tenacity`、`pytest`、`pytest-mock`
- 目录：`src/eventreplay/{adapters,schema,llm,pipeline,measurement}`、`tests/`、`reports/`、`spikes/`（已有）
- 配置：pytest、ruff、`.gitignore`
- ✅ 验收：`pytest` 可跑空套、`python -m eventreplay` 可导入

### M0.2 全局 schema（Pydantic）
- `schema/event.py`：`Event`（`announcement_date`/`reaction_date`/公告时点/`episodes: list[EventEpisode]`/类型/冲击维度/影响方向/关键实体）、`EventEpisode`（自己的 reaction_date/announcement_date/公告时点）
- `schema/measurement.py`：`ImpactMeasurement`（`episode_id`/各窗 CAR/`peak_ar`/`max_drawdown`/`limit_up_days`/`limit_down_days`/`low_confidence`+reason/`persistence_ratio_60_5`/`sig_ar_days`/`aav_days`/`chain_node_id`/`direction`/`tier`/`rationale`/`direction_conflict`）
- `schema/attribution.py`：`AttributionResult`（§3.3 字段）、`chain.py`：`TransmissionChain`、`replay.py`：`Replay`
- ✅ 测试：模型构造/校验/序列化；单事件 episodes 恰 1 元素

### M0.3 SQLite 存储层
- `storage.py`：表 `events`/`replays`/`measurements`/`chains`/`pipeline_runs`/`skip_log`；`events`/`replays` 预留 `embedding_text`/`embedding_model`
- CRUD + upsert；JSON 字段存节点产物
- ✅ 测试：增查改、JSON 字段往返

### M0.4 QuoteAdapter（sina 主源）
- `adapters/quote_adapter.py`：接口 + akshare 实现
  - 个股后复权日线 `stock_zh_a_daily(symbol="sh600519", adjust="hfq", start_date, end_date)`
  - 指数日线 `stock_zh_index_daily(symbol="sh000300")`
  - 交易日历 `tool_trade_date_hist_sina()`
- 本地缓存（按 `code×date_range` hash，避免重复请求/限流）+ tenacity 重试
- 市值辅助：`outstanding_share × close`（从 `stock_zh_a_daily` 推导）
- ✅ 测试：拉真实 600519/000300 一段数据断言非空+字段；缓存命中；交易日历长度

### M0.5 StockMaster
- `adapters/stock_master.py`：
  - 基本信息（`stock_info_a_code_name`，flaky → 重试 + 备用 name_code 接口）
  - 历史曾用名：深市全市场 `stock_info_sz_change_name(symbol="简称变更")` + 单标的 `stock_info_change_name(symbol)`（**异常捕获**：假/无效代码抛 KeyError）
  - 退市：`stock_info_sh_delist()` / `stock_info_sz_delist(symbol="终止上市公司")`
  - 申万行业清单一/二/三级 `sw_index_*_info` + 一级成分股 `index_component_sw(symbol="801001")`
- 关键方法：`resolve(code_or_name) -> Stock | None`（含历史名双源交叉、异常捕获）；`constituents(sw_industry_l1) -> list[Stock]`；`is_real_at(code, date)`（结合上市日/退市/曾用名）
- ✅ 测试：真代码 resolve 命中；假代码 resolve 返回 None（不抛）；历史名（如深发展→平安银行）resolve 命中；一级成分股非空

### M0.6 Claude CLI 封装
- `llm/reasoner.py`：`Reasoner` 接口 + `ClaudeCodeProvider` 实现
- 调用：`claude -p --output-format json --json-schema '<schema>' --bare --disable-slash-commands --permission-mode dontAsk --model '<alias>'`，不传 `--mcp-config`
- 读 `.structured_output` → Pydantic 校验（业务校验失败等同失败）→ 修复式重试（≤2，把校验错误回喂）→ 抛 `LLMNodeFailed`
- 工具函数：`build_json_schema(pydantic_model)`、`extract_structured_output(envelope)`
- **鉴权**：读 `ANTHROPIC_AUTH_TOKEN`+`ANTHROPIC_BASE_URL`（网关）或 `ANTHROPIC_API_KEY`（直连）
- ✅ 测试：mock subprocess 验证 flag 构造 + `.structured_output` 解析 + Pydantic 失败重试 + 最终失败抛异常；**真实冒烟**：`claude -p --bare ... "reply {ok:true}"` 跑通（确认 --bare 不挂起、网关鉴权）

---

## M1 · 影响度量引擎（`measurement/event_study.py`，纯 Python，TDD）

### M1.1 市场模型 OLS
- `fit_market_model(stock_returns, bench_returns, est_window)` → (α̂, β̂, σ_AR)
- ✅ 测试：合成数据断言 α̂/β̂/σ 等于解析解（紧容差 1e-10）

### M1.2 AR / CAR 多窗口
- `abnormal_returns(...)`、`car_over(window)`；窗口 {[0,+1]/[0,+5]/[0,+20]/[0,+60]} + [-1,0]
- 事件日标准化：reaction_date 为 t0；盘前/盘中/盘后/非交易日/长假规则（按交易日历推进）
- ✅ 测试：合成 AR 序列断言 CAR 等于手算；非交易日顺延

### M1.3 显著 AR + 持续天数
- `sig_ar_flag(ar, sigma_ar)` = `|AR|/σ_AR > 1.96`；`sig_ar_days` = 连续显著天数（**标的内时序，禁止跨标的排序——在 docstring + 报告注明**）
- ✅ 测试：构造已知显著/不显著 AR 断言标志与计数

### M1.4 AAV + 持续天数
- `aav(volume, est_window)` = `(V−μ_V)/σ_V`；`aav_days` = 连续 `>2.0` 天数
- ✅ 测试：合成成交量序列断言

### M1.5 涨跌停判定（生效日表）
- `limit_flag(code, date, close, prev_close, board, is_st, is_new_listing)` → up/down/none
- 规则表（spec §4.4）：主板10%/新股前5日(2023-04-10起)/创业板(2020-08-24起20%)/科创(2019-07-22起20%)/北交所(2021-11-15起30%)/ST主板5%(2026-07-06起10%)/ST创科创20%
- 聚合入 `limit_up_days`/`limit_down_days`
- ✅ 测试：**跨时点夹具**——2024 创业板 20%、2019 创业板 10%、2022 主板新股前5日不限、ST 创科创 20% 等

### M1.6 数据清洗
- `clean_and_tag(quotes, listing_date, event_date, ...)` → 清洗后序列 + low_confidence 标记 + reason
- 规则：上市<250 日剔除；thin trading（换手<0.1% 或 零成交日>10%）；估计窗<60 日；停牌剔除（volume=0）；事件窗缺失>20%；ST 按事件期名称；估计窗污染标注；数据取不到 → skip_log
- ✅ 测试：**脏数据夹具**——停牌缺口/新股/估计窗不足/连续涨跌停/低换手；断言清洗规则 + low_confidence 标记 + skip_log 写入

### M1.7 KP(2010) Adjusted BMP（组合层面）
- `adj_bmp(sar_matrix, L)` → Z_adj = Z_BMP / √(1+(L−1)·r̄)，r̄ = 平均截面相关
- ✅ 测试：合成数据断言等于 closed-form（紧容差）

### M1.8 KP(2011) GRANK（手写）
- `grank_z(sar_matrix)` → 基于 GSAR 秩变换的 Z；按 Kolari-Pynnönen 原文 Table 2
- 自适应选择：N≥30 + 正态 + r̄<0.1 → 主推断用 Adjusted BMP；否则 GRANK；N<5 → 仅记录不披露；分歧 → 两 p 值都披露
- ✅ 测试：① 实现等于参考秩变换结果；② **蒙特卡洛 size 检验**（H₀ 下重复仿真，拒绝率 ≈ α±抽样误差）；③ **GRANK vs Adjusted BMP 一致性回归**（正态+大N+无聚集合成数据上 p 值差<0.05）

### M1.9 经济显著性 + 持续性主指标
- `economic_significance(car_0_20, board)` = `|CAR[0,+20]| ≥ 10%`(主板)/`≥20%`(创科)；N<5 只用绝对阈值
- `persistence_ratio` = `CAR[0,+60]/CAR[0,+5]`
- ✅ 测试：阈值边界、board 区分

### M1.10 度量节点封装
- `pipeline/nodes/impact_measurement.py`：输入候选+reaction_date → `ImpactMeasurement[]`（标的×子事件）；剔除/清洗/low_confidence/skip_log；EventEpisode 每子事件各自估 β（重叠→beta_unreliable）
- ✅ 测试：多标的+多子事件端到端度量产物正确

---

## M2 · Pipeline 编排（`pipeline/orchestrator.py`）

### M2.1 节点基类 + 缓存 + 续跑
- `Node` 基类：`run(inputs) -> output`；`cache_key = hash(node_id + node_code_hash + inputs_hash + prompt_hash + model_alias + cli_version + cli_flags_hash)`
- 缓存：SQLite `pipeline_runs`（节点级产物 + 状态）；断点续跑按 node_id 跳过已完成
- **缓存有效边界文档化**：量化节点 + 首推理节点有效；推理→推理下游因 LLM 非确定性命中率低（续跑仍可用）
- ✅ 测试：缓存命中/失效（改 code/flag/model 触发重算）；续跑跳过已完成节点

### M2.2 版本记录
- 每次 Replay 存 `pipeline_version`/`prompt_hash`/`model_alias`/`cli_version` 入 `pipeline_runs`
- ✅ 测试：版本字段写入可查

---

## M3 · 推理节点（6 个，每个：prompt 契约 + Pydantic schema + 降级）

> 每节点统一：定义 `system_prompt` + `user_template` + 输出 Pydantic model → JSON schema → `Reasoner.call()`；mock 契约测试（含 §3.2 三形态降级：单标的 skip / 整节点失败 / 部分节点低置信）+ 真实 CLI 冒烟。

### M3.1 EventIngest
- 输入：事件描述/原文 + 可选 dates → `Event`（含 episodes；单事件退化）
- 降级：失败 → 中止 Replay

### M3.2 IndustryChainReasoning
- 输入：`Event` → 产业链节点图（每节点：自由文本描述 + sw_industry 一级代码）
- 语义护栏：`semantic_match(node_desc, industry_name)` = 关键词/token Jaccard + 关键词词典 ≥1 核心词命中；不达标 → 节点 low_confidence 透传（不整节点降级）；整链 >50% 低置信 → 报告标注
- 降级：整节点失败 → 按 Event 行业标签直查

### M3.3 CandidateGeneration
- 输入：`Event` + 节点 + StockMaster 一级成分股底池 → 候选（每标的 chain_node_id/direction/rationale）
- 检索优先 + StockMaster.resolve（历史名双源交叉、异常捕获）反查
- 降级：失败 → 一级成分股按成交额 top-30/方向预筛

### M3.4 AttributionFiltering（两段式）
- 第一段：经济显著性 + direction 划受益/受损池；direction 与 CAR 符号冲突 → `direction_conflict`/low_confidence（保留不排序）
- 第二段：对两池算 GRANK+Adjusted BMP（N<5 不披露；分歧披露）；产出 `AttributionResult`
- 降级：失败 → 保留全部，分级"未分级"

### M3.5 TransmissionChainSynthesis
- 输入：`{Event, 产业链图, AttributionResult}` → `TransmissionChain`（mermaid 有向图 + 叙述）
- 降级：失败 → 仅图表

### M3.6 ReportAssembly
- 输入：全部产物 → markdown 报告（§7 模板，含 §5 局限披露：基准价格指数 β 偏差/size 暴露/组合统计量分歧/skip_log/行业粒度=一级）
- 降级：失败 → 半结构化 JSON

✅ 每节点测试：mock 返回正常/格式错/语义错三种，断言 Pydantic 解析 + 降级分支正确

---

## M4 · CLI + 集成

### M4.1 typer CLI
- `cli.py`：`replay --desc --date [--reaction-date] [--episode-dates] [--benchmark hs300p] [--from-file]`
- 产物：`reports/<event-id>.md` + SQLite + skip_log

### M4.2 端到端串联
- `orchestrator.replay(event_input)` 串 7 节点；节点间传结构化对象
- ✅ 测试：mock 全部推理节点，断言 7 节点串联产物完整、缓存/续跑生效

---

## M5 · 验收（5 种子事件）

### M5.1 种子 fixture 固化
- `tests/fixtures/seeds/{trade_war_2018,covid_2020,double_reduction_2021,chatgpt_2023,xin_guo_jiu_tiao_2024}.yaml`
- 每事件：`expected_beneficiary_set`/`expected_victim_set`（每方向 ≥5，spike 据实选定；#4 受损池可空）；reaction_date 已定（spec §6）

### M5.2 端到端冒烟
- 对 5 事件真实跑全 pipeline（含真实 CLI 推理节点 + 真实 sina 数据）
- 断言：报告生成；受益/受损池分别 `ceil(N×0.5)` 命中（空池跳过）+ direction 一致；度量非空
- ⚠️ 已知：贸易战/新冠短窗可能全负（系统性暴跌），主断言落 [0,+20]/[0,+60]

### M5.3 报告人工抽查
- 5 份报告人工复核：产业链逻辑链合理性、受益/受损标的可解释性、局限披露完整

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| sina 限流/偶发不可达 | QuoteAdapter 缓存 + tenacity 重试；网络是外部依赖 |
| GRANK 手写正确性 | M1.8 三重测试（参考秩变换 + 蒙特卡洛 size + 与 Adjusted BMP 一致性） |
| LLM 非确定性 | 缓存 + 版本追溯；端到端冒烟允许定性断言 |
| `claude -p` 不带 `--bare` 挂起 | 封装强制 `--bare`（M0.6 冒烟验证） |
| 一级成分股粒度粗（漏细分标的） | 报告披露粒度；AttributionFiltering 经济显著性兜底 |

---

## 执行顺序建议

M0.1 → M0.2 → M0.3 → M0.4 → M0.5 → M0.6 →（M1.1…M1.10）→ M2 →（M3.1…M3.6）→ M4 → M5

M0.6（CLI 封装）可与 M1 并行（M1 不依赖 LLM）。M1 是纯量化、可完全 TDD、零 LLM 成本，建议优先做扎实。

---

## 实现期偏离与已知限制（M5 记录）

实现中据真实数据/环境做出的偏离（已记入 spike-results 或代码注释）：

1. **行业成分股不可得**（spike §6）：`index_component_sw` 对真实行业代码全 KeyError → CandidateGen 降级为"LLM 提议 + StockMaster.resolve 反查"（反幻觉护栏仍有效）。报告披露"行业覆盖依赖 LLM 召回 + 真实性校验"。
2. **AttributionFiltering 实现为确定性量化节点**（非 LLM）：两段式过滤 + 强弱分级是规则化的算术，不需 LLM 推理；MVP 用确定性实现（更稳定、可测、零 LLM 成本）。
3. **池级统计量简化**：spec §4.3 的 GRANK/Adjusted BMP 需 SAR 矩阵贯通全 pipeline；MVP 用"横截面 CAR[0,+20] t 检验"作辅证披露（已在 `tier_definitions.pool_stat_method` 标注）。完整 GRANK 留后续。
4. **进程内缓存 vs 跨进程续跑**：`Node.run_cached` 命中进程内缓存（集成测试已验证同进程重跑命中）；SQLite 持久缓存为 write-only（Pydantic 类型对象的跨进程反序列化未实现，续跑留后续）。【2026-10-19 回写：本条为 2026-06-30 时点状态；后续迭代已补 SQLite 缓存读路径与类型重建（`pipeline/node.py:84-145`，`tests/test_fixes_regression.py` 覆盖），重跑同一事件可等效续跑，但无独立 resume 命令入口，见下方「实现状态」】
5. **事件窗停牌失真**（300010 双减案例）：K12 股在事件窗内停牌，清洗去 volume=0 日后窗口压缩、CAR 可能失真（如 300010 出现 +13% 异常）。当前未专门标 low_confidence；改进方向：检测事件窗内停牌占比→标低置信。
6. **全量单事件真实 smoke 为慢速手动测试**（`tests/test_real_smoke.py`，需鉴权环境变量、~7-8min、6 次 claude 调用）。组件级真实验证已通过（LLM 节点 + impact 分别真实跑通）；GLM 网关对大 prompt（如 report 节点）偶发超时，重试即可。

---

## 实现状态（2026-10-19 回写）

> 2026-10-19 对 Phase 1 落地情况逐里程碑复核。上方状态行与「实现期偏离与已知限制（M5 记录）」保留 2026-06-30 原记录；全局事实与偏离清单另见 [总计划 §10](../specs/2026-06-30-event-replay-master-plan.md)。

### 逐里程碑结果

| 里程碑 | 结果 | 落地证据（2026-10-19） |
|---|---|---|
| M0.1 项目脚手架 | **完成** | `pyproject.toml` 实际要求 Python `>=3.10`（运行环境 3.10.10）；`src/eventreplay` 60 个 `.py` / 7,933 行；`tests` 32 个测试文件 / 6,646 行；`spikes` 39 个脚本 / 3,540 行 |
| M0.2 全局 schema | **完成** | `schema/event.py`（`Event`/`EventEpisode`）、`measurement.py`、`attribution.py`、`chain.py`、`replay.py` 全部落地；`Event.episodes` 单事件恰 1 元素（`schema/event.py:36-44`） |
| M0.3 SQLite 存储层 | **完成** | `storage.py`；本地库 `data/eventreplay.db`（11.8MB，9 表） |
| M0.4 QuoteAdapter | **完成** | `adapters/quote_adapter.py`（sina 主源 + 本地缓存 + tenacity 重试） |
| M0.5 StockMaster | **完成（超越）** | `adapters/stock_master.py`；成分股召回源后续升级为 tushare 申万三级 + curated `ThemeIndex` 兜底 + LLM 提议 resolve 反查（`pipeline/orchestrator.py:28-35`、`:56-59`），强于计划的一级成分股底池 |
| M0.6 Claude CLI 封装 | **完成** | `llm/reasoner.py`（`--bare` + `--json-schema` + Pydantic 校验 + 修复式重试）；环境 claude CLI 2.1.201 |
| M1.1–M1.6 度量引擎 | **完成** | `measurement/`（市场模型、CAR 多窗口、显著 AR、AAV、涨跌停规则表、数据清洗）落地；窗口集另含 +90（`schema/measurement.py:32`，超出计划的窗口集合） |
| M1.7 Adjusted BMP / M1.8 GRANK | **偏离（实现但未接线）** | `measurement/significance.py:56` adjusted_bmp、`:76` generalized_rank_z、`:108` choose_pool_stat 已实现并有测试；但归因主线仍用 scipy t 检验（`pipeline/nodes/attribution.py:56`），61 条 `attribution_results` 的 `PoolStat.adj_bmp_*` 恒为空，报告注「简化 t 检验」（`measurement/report_template.py:148,174`） |
| M1.9 经济显著性/持续性 | **完成** | `measurement/significance.py:29-37`；`schema/measurement.py:45` persistence_ratio |
| M1.10 度量节点封装 | **完成** | `pipeline/nodes/impact.py`；库内 `measurements` 2,746 行 |
| M2.1 缓存 + 续跑 | **完成（口径更新）** | `pipeline/node.py:84-112`：进程内缓存 + SQLite 节点级持久缓存（读写均支持，按 `output_type` 重建），`pipeline_runs` 525 行。上方 M5 记录第 4 条「SQLite 持久缓存为 write-only」为当时状态，后续实现已补读路径；另未单独提供「续跑」命令入口 |
| M2.2 版本记录 | **完成** | `pipeline_runs` 记录 node_id/cache_key/status（`storage.py:57-68`） |
| M3.1–M3.6 六推理节点 | **完成** | 7 节点全部落地（`pipeline/nodes/ingest.py`、`chain.py`、`candidate.py`、`synthesis.py`、`report.py` + 确定性量化节点 `attribution.py`/`impact.py`）；AttributionFiltering 按 M5 记录第 2 条实现为确定性量化节点 |
| M4.1 typer CLI | **部分偏离** | 实际入口 `replay --desc --date [--reaction-date] [--benchmark] [--event-id] [--model] [--market] [--db]`（`cli.py:92-102`）；**无 `--episode-dates` / `--from-file`**；报告命名实际为 `{日期}_{事件描述}.md`（`measurement/report_template.py:16-24`、`pipeline/orchestrator.py:179-181`），非计划的 `reports/<event-id>.md`，`--event-id` 只作事件 id 不改文件名 |
| M4.2 端到端串联 | **完成** | `pipeline/orchestrator.py` 串 7 节点 + 报告落盘；已产出 64 份复盘报告（`reports/`，覆盖 2016–2026） |
| M5.1 种子 fixture 固化 | **未做** | `tests/fixtures/seeds/*.yaml` 未落地；验收脚本在 `spikes/accept_5seeds.py` |
| M5.2 端到端冒烟 | **完成（2026-10-19 补跑）** | 历史事件已真实跑通并入库（61 events，已产出 64 份复盘报告）；`tests/test_real_smoke.py`（真实 LLM 端到端）**2026-10-19 实跑通过：1 passed / 494.47s**，7/7 节点 `done`、31 条 measurements、完整报告生成 |
| M5.3 报告人工抽查 | **未做（本次复核）** | 报告模板与产物齐备，本轮未重新做人工抽查 |

### 实现期偏离（2026-10-19 复核）

1. **5 个种子验收夹具未落地**（M5.1）：`tests/fixtures/seeds/*.yaml` 不存在，验收逻辑改由 `spikes/accept_5seeds.py` 承载。
2. **多 episode 无入口**（M4.1）：CLI 无 `--episode-dates` / `--from-file`，多 episode 仅 schema + 度量支持（`schema/event.py:36`、`pipeline/nodes/impact.py:31`），实际 replay 入口仍是单事件 `--desc` + `--date`。
3. **报告命名与计划不符**（M4.1）：实际 `{日期}_{事件描述}.md`（`measurement/report_template.py:16-24`），计划写的是 `reports/<event-id>.md`。
4. **组合统计量仍为简化 t 检验**（M1.7/M1.8）：GRANK / Adjusted BMP 有实现但未接入归因，`PoolStat.adj_bmp_*` 恒空；详见上文「实现期偏离与已知限制（M5 记录）」第 3 条与 [总计划 §10 ④](../specs/2026-06-30-event-replay-master-plan.md)。
5. **`replays` 表 0 行**：可复现记录缺失，属存量数据重建遗留（历史事件经批量脚本重建，未走 replay 状态写入），不影响报告内容与规律库检索；不伪造回填。

### 现状补充（2026-10-19）

- 离线测试：`pytest --ignore=tests/test_real_smoke.py` → **342 passed / 8 skipped / 0 failed**（约 30s，纯离线；含收尾新增 4 个回归测试）；8 skip = 5 个真实网络 smoke + 2 个缺 `TUSHARE_TOKEN` + 1 个数据条件。
- CLI 实际 8 子命令：`replay`、`extract-pattern`、`find-similar`、`refresh-sw`、`forward`、`mine-events`、`batch-replay`、`monitor`；`python -m eventreplay` 收尾前不可用，本次收尾补 `src/eventreplay/__main__.py`。
- 数据与产物：`data/eventreplay.db`（11.8MB，9 表）`events` 61 / `chains` 61 / `attribution_results` 61 / `measurements` 2,746 / `patterns` 58 / `pipeline_runs` 525 / `seen_events` 83 / `skip_log` 673 / `replays` 0；`reports/` 96 份 `.md` = 64 复盘 + 22 前瞻 + 10 监测（按报告首行标题判定）。
- 环境：Python 3.10.10；claude CLI 2.1.201；`.env` 9 键均非空；`.env.example` 曾缺 3 键（`EMBEDDING_BACKEND`/`FEISHU_WEBHOOK`/`MONITOR_INTERVAL`，本次回写补齐）。
