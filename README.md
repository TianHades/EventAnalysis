# EventAnalysis · 重大事件复盘分析与前瞻系统

对历史重大事件复盘，分析其对产业链和 A 股标的的影响幅度与持续性，总结规律链传导关系；对新事件做前瞻映射预判；定时监测新闻自动触发前瞻并推送。

## 系统能力

| 能力 | 命令 | 说明 |
|------|------|------|
| **复盘** | `replay` | 事件 → 7 节点 pipeline（事件解析→产业链→标的圈定→CAR 度量→归因→报告） |
| **规律库** | `extract-pattern` / `find-similar` | 从复盘提炼 Pattern（事件签名+传导模板+典型标的+5 维定性+向量），支持语义检索 |
| **前瞻** | `forward` | 新事件 → 检索相似历史 Pattern → 预判受益/受损标的 + 预期 CAR + 历史定性对比 |
| **监测** | `monitor` | 多源新闻采集 → LLM 事件抽取 → 阈值过滤 → 高分事件自动前瞻 → 飞书/企微推送 |
| **批量挖掘** | `mine-events` / `batch-replay` | LLM 列候选事件 → 批量复盘 + Pattern 入库 |

## 规律库现状

**58 个 Pattern**，覆盖 **2016-2025 十年**外部系统性冲击事件，全带 5 维定性标签（程度/范围/持续/显著/超预期），全 TEXT 报告，2,864+ measurements。

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

```bash
pip install -e ".[dev]"
```

## 配置

复制 [.env.example](.env.example) 为 `.env` 并填写：

```ini
# LLM 网关（必需）
ANTHROPIC_AUTH_TOKEN=你的_token
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic

# CLI 默认值（可选）
EVENTREPLAY_MODEL=sonnet
EVENTREPLAY_BENCHMARK=sh000300
EVENTREPLAY_DB=data/eventreplay.db
EMBEDDING_BACKEND=local  # 强制 bge-small-zh-v1.5(512 维)

# 监测系统（可选）
MONITOR_INTERVAL=240              # 扫描间隔（分钟，默认 4 小时）
FEISHU_WEBHOOK=                   # 飞书机器人 webhook（留空=不推送）

# tushare pro（可选，权威成分股源）
TUSHARE_TOKEN=
```

## 命令

### 复盘（单个历史事件）

```bash
eventreplay replay --desc "事件描述" --date 2024-09-24 --market A股 --model haiku
```

产出：`reports/{日期}_{事件描述}.md` + 结构化数据入 SQLite + Pattern 自动入库。

### 前瞻（新事件预判）

```bash
# 盘前前瞻（默认读 .env FEISHU_WEBHOOK，自动推送飞书）
eventreplay forward --desc "中东霍尔木兹海峡遭封锁" --date 2026-07-29

# 盘后校准（拉实际 [+1]/[+5] 校准预判）
eventreplay forward --desc "..." --date ... --mode post
```

产出：前瞻报告（## 1 摘要 + ## 2 历史定性对比 + ## 3 预判标的 + ## 4 传导逻辑 + ## 5 风险）。

### 监测（定时新闻 → 自动前瞻 → 推送）

```bash
# 默认：4 小时扫描 + 多源融合 + 飞书推送（读 .env）
eventreplay monitor

# 自定义
eventreplay monitor --interval 60 --source multi --significance 3 --a-share 4 --surprise 3
```

**触发条件**：significance ≥ 3（主线级）AND a_share ≥ 4（A 股可达）AND surprise ≥ 3（突发/超预期）。

**无触发时**：自动输出本轮最高分事件摘要（让你感知市场动态）。

**推送渠道**：FileChannel（文件）+ FeishuChannel（飞书）+ WebhookChannel（企微/钉钉）。

### 检索相似历史 Pattern

```bash
eventreplay find-similar --desc "美联储加息" --shock-dimension 流动性 --top-k 5
```

### 批量挖掘 + 复盘

```bash
# 生成候选 JSON（人工搜集 + make_dedup_key）
PYTHONPATH=src python spikes/build_mining_2024.py

# 批量复盘入库
eventreplay batch-replay --job-file data/mining_candidates_2024-01-01_2024-12-31.json --market A股 --model haiku
```

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
# 日常测试（跳过触网/真实 LLM 的 smoke）
pytest --ignore=tests/test_real_smoke.py

# 含真实数据/LLM
pytest
```

## 架构

**Python 主编排 + Claude Code CLI 推理节点 + 显式 pipeline**：

- 数值计算（CAR/t 值/异常成交量/持续性）走 Python（scipy）
- LLM 推理（事件抽取/产业链归因/前瞻映射）经 `claude -p` CLI（headless）
- Embedding 用本地 bge-small-zh-v1.5（离线零 token）
- 数据主源 sina（个股 `stock_zh_a_daily`、指数 `stock_zh_index_daily`）

**关键设计**：
- ReportNode：模板+LLM 分工（模板生成传导/标的表/统计，LLM 只写 ## 1 摘要 + ## 4 判读），避开网关超时
- PatternQualitative：5 维事件级定性（程度/范围/持续/显著/超预期），供前瞻对比参考
- source.lookup：三级+一级申万行业成分股匹配，最大化候选召回
- 网络超时保护：tushare `_members` + akshare `_fetch_*` 全部 daemon 线程 + 60s 超时
- 报告文件名：`{日期}_{事件描述}.md`（可读，event_id 保持 hash 不影响 DB）

## 文档

规格与实施计划：`docs/superpowers/specs/` + `docs/superpowers/plans/`

核心 spec：
- [总计划](docs/superpowers/specs/2026-06-30-event-replay-master-plan.md)（6 阶段程序级计划）
- [Phase 1 复盘 MVP](docs/superpowers/specs/2026-06-30-phase1-replay-mvp.md)
- [数据 spike 结果](docs/superpowers/specs/2026-06-30-spike-results.md)（数据源/CLI 实跑 ground truth）

> 数据源用 sina（`stock_zh_a_daily` 等）；eastmoney/baostock 在开发环境不可达。LLM 推理节点经 Claude Code CLI（`--bare` 必须）。详见 spike-results。
