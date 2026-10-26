# EventAnalysis · 重大事件复盘分析与前瞻系统

对历史重大事件复盘，分析其对产业链和 A 股/港股标的的影响幅度与持续性，总结规律链传导关系；对新事件做前瞻映射预判；定时监测新闻自动触发前瞻并推送。

## 系统能力

| 能力 | 命令 | 说明 |
|------|------|------|
| **复盘** | `replay` | 事件 → 7 节点 pipeline（事件解析→产业链→标的圈定→CAR 度量→归因→传导链综合→报告） |
| **规律库** | `extract-pattern` / `find-similar` | 从复盘提炼 Pattern（事件签名+传导模板+典型标的+5 维定性+向量），支持语义检索 |
| **前瞻** | `forward` | 新事件 → 检索相似历史 Pattern → 预判受益/受损标的 + 预期 CAR + 历史定性对比 |
| **监测** | `monitor` | 多源新闻采集 → LLM 事件抽取 → 阈值过滤 → 高分事件自动前瞻 → 飞书/企微推送；`--once` 单轮即退 |
| **批量挖掘** | `mine-events` / `batch-replay` | LLM 多阶段列候选事件（`--start --end --top`）→ 人工审核 → 批量复盘 + Pattern 自动入库 |
| **多市场（港股）** | `--market 港股` | 港股复用同一链路：`HKQuoteAdapter` + `HKStockMaster`，基准未显式指定时自动切 HSI，港股无涨跌停；也接受 `hk`/`hkex` 等别名 |
| **申万缓存** | `refresh-sw` | 一次性预拉申万三级成分股到本地缓存，之后复盘零 tushare 网络调用 |

> 当前覆盖 A 股（默认）与港股；美股 / 港股 GEM / 港股通未接入（见「已知限制」）。

## 规律库现状

**58 个 Pattern**，覆盖 **2016-2025 十年**外部系统性冲击事件，全带 5 维定性标签（程度/范围/持续/显著/超预期）与 embedding 向量（58/58），Pattern 数据以 TEXT（JSON）持久化；数据库 measurements 共 **2,746** 条（events 61 / patterns 58 / pipeline_runs 531；replays 0，见「已知限制」）。

累计产出 **100 份报告**（2026-10-19 统计，含当日 monitor 实跑新增），覆盖 2016–2026（复盘、前瞻、监测三类产物；含港股复盘示例 `reports/hk_tencent_dump.md`）。

| 年份 | Pattern | 代表事件 |
|------|---------|---------|
| 2016 | 10 | 供给侧改革、英国脱欧、AlphaGo |
| 2017 | 7 | 雄安新区、MSCI 纳入、地条钢清除 |
| 2018 | 3 | 中美贸易战开端、中兴制裁 |
| 2019 | 4 | 贸易战升级、华为实体清单 |
| 2020 | 4 | 新冠疫情、联储无限QE、油价崩盘 |
| 2021 | 6 | 双减政策、互联网反垄断、核心资产见顶 |
| 2022 | 7 | 俄乌战争、美联储加息周期、芯片法案 |
| 2023 | 8 | ChatGPT、华为 Mate60、全面注册制 |
| 2024 | 4 | 924 组合拳、量化微盘崩、DeepSeek V3 |
| 2025 | 5 | DeepSeek R1、印巴空战、中欧电动车协议 |

## 安装

环境要求：**Python 3.10+**（`pyproject.toml` 声明 `requires-python >=3.10`）；**Claude Code CLI** 需在 PATH（LLM 推理节点经 `claude -p` headless 调用，实测 2.1.201）。

```bash
pip install -e ".[dev]"
```

安装后 `eventreplay <子命令>` 即 console script；也可用 `python -m eventreplay <子命令>`（同一入口，效果等价；未安装时可用 `PYTHONPATH=src python -m eventreplay ...`）。

## 新设备部署（git clone 后）

仓库自带规律库静态快照 `seed/eventreplay.db`（58 Pattern / 2,746 measurements / 61 events，2026-10-19 时点）和主题召回索引 `data/themes.yaml`；本地运行库 `data/eventreplay.db`、`cache/` 与顶层 `reports/*.md` 不入 git（`reports/monitoring/` 历史归档已入库）。

```bash
# 1) 安装（Python 3.10+；Claude Code CLI 需在 PATH）
pip install -e ".[dev]"

# 2) 配置 .env：复制 .env.example 后填写 9 个键
#    注意 EMBEDDING_BACKEND=local —— 库内 embedding 为本地 bge-small-zh-v1.5(512 维)；
#    若锁成智谱 2048 维，相似检索会全部 0 分、forward 退化为纯定性推理
cp .env.example .env          # Windows: Copy-Item .env.example .env

# 3) 灌入规律库 seed（已有 data/eventreplay.db 时跳过；-Force 覆盖）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
# 或手动：New-Item -ItemType Directory -Force data | Out-Null; Copy-Item seed\eventreplay.db data\eventreplay.db

# 4) 验收
eventreplay find-similar --desc "美联储加息" --top-k 3   # 应返回 3 条 Pattern
pytest --ignore=tests/test_real_smoke.py                 # 离线全量测试
```

依赖：Python 3.10+、Claude Code CLI（`claude -p --bare`）、首次 embed 会下载本地模型 `BAAI/bge-small-zh-v1.5`（走 HF mirror，需网络）。规律库更新（主力设备）：`Copy-Item data/eventreplay.db seed/eventreplay.db` → 提交 `seed/eventreplay.db`；运行产物继续留在本地不入库。

## 配置

复制 [.env.example](.env.example) 为 `.env` 并填写（共 9 个键）：

```ini
# LLM 网关（必需）
ANTHROPIC_AUTH_TOKEN=你的_token
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic

# CLI 默认值（可选）
EVENTREPLAY_MODEL=sonnet
EVENTREPLAY_BENCHMARK=sh000300
EVENTREPLAY_DB=data/eventreplay.db
EMBEDDING_BACKEND=auto       # auto=在线优先、本地兜底（见下）；可强制 zhipu / local

# 监测系统（可选）
MONITOR_INTERVAL=240              # 扫描间隔（分钟，默认 4 小时）
FEISHU_WEBHOOK=                   # 飞书机器人 webhook（留空=不推送）

# tushare pro（可选，权威成分股源）
TUSHARE_TOKEN=
```

> **Embedding 后端**（`EMBEDDING_BACKEND`，默认 `auto`）：首次 embed 时探测——有智谱 Key（优先 `ZHIPU_API_KEY`，未设置时回退 `ANTHROPIC_AUTH_TOKEN`）且智谱 `embedding-3` 可达，则锁定在线（2048 维）；否则回退本地 `bge-small-zh-v1.5`（512 维，离线零 token）。锁定结果写入 `cache/embedder_backend.txt`，**锁定后不再切换**（避免规律库向量维度漂移）；设 `zhipu` / `local` 可强制。

## 命令

### 预拉申万成分股缓存（可选，首次使用建议）

```bash
eventreplay refresh-sw
```

一次性预拉全部申万三级行业成分股到 `cache/tushare/sw_members.pkl`（需 `.env` 配 `TUSHARE_TOKEN`）；之后 `replay` 的候选召回零 tushare 网络调用。

### 复盘（单个历史事件）

```bash
# A 股（默认）
eventreplay replay --desc "事件描述" --date 2024-09-24 --market A股 --model haiku

# 港股：基准未显式指定时自动切 HSI
eventreplay replay --desc "腾讯大股东减持" --date 2023-01-25 --market 港股 --model haiku
```

产出：`reports/{日期}_{事件描述}.md` + 结构化数据入 SQLite。港股复用同一 7 节点链路（`HKQuoteAdapter` / `HKStockMaster`，港股无涨跌停），示例见 `reports/hk_tencent_dump.md`。

> `--market` 接受 `A股`/`港股`，也接受 `a`、`cn`、`ashare`、`hk`、`hkex`、`hongkong` 等别名（大小写不敏感）；**未知值会直接报错退出**（不再静默按 A 股处理）。复盘/前瞻报告末尾统一附"研究用途，非投资建议"合规声明。

### 单事件规律提炼（Pattern）

```bash
# 单事件复盘后手动提炼；<id> 即 replay 时的 --event-id（默认 evt）
eventreplay extract-pattern --replay-id <id>
```

> Pattern 提炼是独立一步；`batch-replay` 批量链路才会对每个事件自动提炼入库。

### 前瞻（新事件预判）

```bash
# 盘前前瞻（默认读 .env FEISHU_WEBHOOK，自动推送飞书）
eventreplay forward --desc "中东霍尔木兹海峡遭封锁" --date 2026-07-29

# 盘后校准（拉实际 [+1]/[+5] 校准预判）
eventreplay forward --desc "..." --date ... --mode post

# 港股前瞻
eventreplay forward --desc "..." --date ... --market 港股
```

产出：前瞻报告（## 1 摘要 + ## 2 历史定性对比 + ## 3 预判标的 + ## 4 传导逻辑 + ## 5 风险）。

### 监测（定时新闻 → 自动前瞻 → 推送）

```bash
# 默认：4 小时扫描 + 多源融合 + 飞书推送（读 .env）
eventreplay monitor

# 只跑一轮（采集→抽取→前瞻→推送）后退出：适合盘前定时任务/调试
eventreplay monitor --once

# 自定义
eventreplay monitor --interval 60 --source multi --significance 3 --a-share 4 --surprise 3
```

> 关闭推送（飞书/企微）用 `--feishu-webhook=` / 不传 `--push-webhook`；注意 **PowerShell 下不要写 `--feishu-webhook ""`**（空字符串会被吞成缺值，仍用 .env 默认值）。

**触发条件**：significance ≥ 3（主线级）AND a_share ≥ 4（A 股可达）AND surprise ≥ 3（突发/超预期）。

**无触发时**：自动向飞书/企微推送一条「本轮最高分事件」摘要卡片（同时输出到控制台），避免整天静默；摘要只走实时渠道、不标记已推送，不影响后续重试。整轮没识别出事件时推一条「未识别出重大事件」心跳。

**推送渠道**：FileChannel（文件）+ FeishuChannel（飞书）+ WebhookChannel（企微/钉钉）。

> 监测链路当前固定 A 股（`make_adapters("A股")`），港股监测未接入。

### 检索相似历史 Pattern

```bash
eventreplay find-similar --desc "美联储加息" --shock-dimension 流动性 --top-k 5
```

### 批量挖掘 + 复盘

```bash
# 1) LLM 多阶段挖掘候选：阶段1 广撒网 → 硬过滤 → 阶段2 评估，落盘 raw/filtered/final 三个 JSON 供追溯
eventreplay mine-events --start 2024-01-01 --end 2024-12-31 --top 5 --market A股 --model haiku
# 可选：--industry 半导体 --shock-dimension 政策 --raw-top 20

# 2) 人工审核 data/mining_candidates_2024-01-01_2024-12-31.json（可编辑）

# 3) 批量复盘 + 自动提炼 Pattern 入库
eventreplay batch-replay --job-file data/mining_candidates_2024-01-01_2024-12-31.json --market A股 --model haiku
```

> 备选：仓库内 `spikes/` 下的手写候选脚本（如 `PYTHONPATH=src python spikes/build_mining_2024.py`）可生成同格式 JSON，作为可选的批量挖掘前置方案；正式入口以 `mine-events` 为准。

## 新闻源

| `--source` | 源 | 特点 |
|-----------|-----|------|
| `akshare` | 财新主流 | ~100 条实时 |
| `jiuyan` | 韭研公社 | 事件+标的结构化 |
| `cctv` | 央视新闻联播 | 权威政策首发 |
| `cls` | 财联社全球 | A 股实时快讯 |
| `jin10` | 金十数据 | 全球宏观地缘 |
| `multi` | 4 源融合（默认） | 去重合并全覆盖 |

## 开发

```bash
# 日常测试（离线，跳过触网/真实 LLM 的 smoke；约 30s）
pytest --ignore=tests/test_real_smoke.py

# 真实 LLM 端到端 smoke（约 8 分钟；触网 + 需 .env 鉴权；该测试会自行加载 .env）
pytest tests/test_real_smoke.py -v
# 2026-10-19 实测：1 passed in 494.47s（7/7 节点 done、31 measurements、完整报告生成）
```

## 架构

**Python 主编排 + Claude Code CLI 推理节点 + 显式 7 节点 pipeline**：

- 7 节点：事件解析（EventIngest）→ 产业链（IndustryChain）→ 标的圈定（Candidate）→ CAR 度量（ImpactMeasurement）→ 归因（Attribution）→ 传导链综合（TransmissionChain，`nodes/synthesis.py`）→ 报告（Report）
- 数值计算（CAR/t 值/异常成交量/持续性）走 Python（scipy）
- LLM 推理（事件抽取/产业链归因/前瞻映射）经 `claude -p` CLI（headless，`--bare` 必须）
- Embedding 默认 `auto`：智谱 `embedding-3`（2048 维）可达则在线、否则本地 `bge-small-zh-v1.5`（512 维），首次锁定后不切换（见「配置」）
- 数据主源 sina（个股 `stock_zh_a_daily`、指数 `stock_zh_index_daily`）；港股经 `HKQuoteAdapter`（`stock_hk_daily` / `stock_hk_index_daily_sina`）

**关键设计**：
- ReportNode：模板+LLM 分工（模板生成传导/标的表/统计，LLM 只写 ## 1 摘要 + ## 4 判读），避开网关超时
- PatternQualitative：5 维事件级定性（程度/范围/持续/显著/超预期），供前瞻对比参考
- source.lookup：申万三级 + 一级行业成分股匹配（tushare 权威 + curated ThemeIndex 兜底），最大化候选召回
- 网络超时保护：tushare `_members` 与 akshare 调用均经 daemon 线程包装 + 60s 超时（`adapters/_net.py`）
- 报告文件名：`{日期}_{事件描述}.md`（描述截断 30 字，可读；event_id 不影响报告路径）
- 合规披露：复盘/前瞻报告末尾统一追加"研究用途，非投资建议"（`measurement/report_template.py` 的 `DISCLAIMER`）；飞书卡片底部同义备注

## 文档

规格与实施计划：`docs/superpowers/specs/` + `docs/superpowers/plans/`（2026-10-19 已回写实现状态与实现期偏差）。

**规格（7 份）**
- [总计划](docs/superpowers/specs/2026-06-30-event-replay-master-plan.md)（五阶段程序级计划；Phase 0 基座并入 Phase 1）
- [Phase 1 Spec：复盘 MVP](docs/superpowers/specs/2026-06-30-phase1-replay-mvp.md)
- [Phase 2 Spec：规律库](docs/superpowers/specs/2026-07-01-phase2-pattern-library.md)
- [Phase 3 Spec：前瞻映射](docs/superpowers/specs/2026-07-03-phase3-forward-mapping.md)
- [Phase 4 Spec：事件采集与监测推送](docs/superpowers/specs/2026-07-04-phase4-monitoring-mining.md)
- [Phase 5 Spec：港股市场接入](docs/superpowers/specs/2026-07-04-phase5-hk-market.md)
- [数据可用性 Spike 结果](docs/superpowers/specs/2026-06-30-spike-results.md)（数据源/CLI 实跑 ground truth）

**实施计划（5 份）**
- [Phase 1 实施计划：复盘 MVP](docs/superpowers/plans/2026-06-30-phase1-implementation-plan.md)
- [Phase 2 实施计划：规律库](docs/superpowers/plans/2026-07-01-phase2-implementation-plan.md)
- [Phase 3 实施计划：前瞻映射](docs/superpowers/plans/2026-07-03-phase3-implementation-plan.md)
- [Phase 4 实施计划：事件采集与监测推送](docs/superpowers/plans/2026-07-04-phase4-implementation-plan.md)
- [Phase 5 实施计划：港股市场接入](docs/superpowers/plans/2026-07-04-phase5-implementation-plan.md)

> 数据源用 sina（`stock_zh_a_daily` 等）；eastmoney/baostock 在开发环境不可达。LLM 推理节点经 Claude Code CLI（`--bare` 必须）。详见 spike-results。

## 已知限制（2026-10-20）

- **组合统计量未接入归因**：KP(2011) GRANK 与 KP(2010) Adjusted BMP 已在 `measurement/significance.py` 实现并有单测，但归因节点主链路仍用横截面 CAR[0,+20] t 检验（`tier_definitions.pool_stat_method` 标注简化），组合推断尚未接入。
- **定性校准未接主链路**：`calibrate_qualitative`（五维定性事后对账）已实现，但 forward 链路只调用数值校准 `InitialCalibrationNode`，定性对账暂未接线。
- **多 episode 无 CLI 入口**：`--episode-dates` / `--from-file` 在 Phase 1 spec 中设计但 CLI 未实现，当前无法直接跑多子事件事件集。
- **可复现记录缺失**：`replays` 表当前 0 条（events 61 / measurements 2,746 / patterns 58 有数据），历史复盘未回填 pipeline/prompt 版本与各节点产物。
- **多市场仅港股**：美股、港股 GEM、港股通南向均未接入；`monitor` 监测链路固定 A 股。
- **种子 fixture 未落地**：`tests/fixtures/seeds/` 的种子事件 yaml 未实现，单事件真实 smoke 仍为手动慢测（`tests/test_real_smoke.py`，需鉴权环境变量，约 7–8 分钟、6 次 claude 调用；2026-10-19 实跑通过，见「开发」）。
- **存量报告未回填免责声明**：2026-10-20 前的 100 份历史报告与 `report_assembly` 节点缓存不会自动补新声明（缓存键不含模板源码 hash），如需旧报告带上声明需清缓存重跑。
