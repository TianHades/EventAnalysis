# Phase 4 Spec：事件采集与监测推送（Event Ingestion & Monitoring）

- **状态**：已实现（2026-10-19 状态回写；设计与实现的偏离见文末）
- **日期**：2026-07-04
- **所属总计划**：[2026-06-30-event-replay-master-plan.md](./2026-06-30-event-replay-master-plan.md) 的 Phase 4
- **依赖**：Phase 1（ReplayOrchestrator / QuoteAdapter / StockMaster / run_cached）；Phase 2（PatternService.extract_pattern / PatternStore）；Phase 3（ForwardOrchestrator）
- **关键决策**：① 双功能共享事件采集/抽取/打分层 ② 功能2（历史建库）优先于功能1（监测推送）——规律库空是 Phase 3 瓶颈 ③ 功能2事件源用 LLM 知识 + 用户审核，不依赖历史新闻归档 ④ 功能1新闻源待 spike，MVP 可先文件/LLM 知识输入【2026-10-19 回写：此处“待 spike”与 §4.1“spike 确认”并存系文档历史；实际新闻源已实现为 akshare+cctv+cls+jin10+jiuyan 融合，见文末】 ⑤ 批量 replay 串行 + 断点续跑 ⑥ 事件打分：重大性 + A股可达性 + 冲击维度多样性

---

## 1. 目标与范围

### 1.1 目标

把"手动输入事件"升级为"事件采集驱动"系统，具备两个互补功能：

- **功能2 历史建库**：指定日期范围 → 搜寻关键事件 → 批量 replay + extract-pattern → 充实 Replay 库 + Pattern 库。**解决 Phase 3 规律库为空、前瞻只能走 qualitative 的瓶颈**。
- **功能1 监测推送**：定时采集最新新闻 → 抽取打分 → 高分事件自动 forward → 推送前瞻预判。全自动前瞻闭环。

### 1.2 范围

- ✅ **NewsAdapter**：新闻采集（功能1实时源）
- ✅ **EventExtractor**：新闻/候选 → 结构化 Event + 重大性打分（共享）
- ✅ **EventDedup**：事件去重（同事件多源/多次）
- ✅ **HistoricalEventMiner**（功能2）：日期范围 + 过滤 → LLM 知识列候选事件 + 用户审核
- ✅ **BatchReplayRunner**（功能2）：批量 replay + extract-pattern + 断点续跑 + 进度
- ✅ **MonitoringPusher**（功能1）：APScheduler 定时采集 → 高分 forward → 推送
- ✅ **PushChannel**：推送渠道抽象（webhook + 本地文件，MVP）
- ✅ CLI `mine-events`（功能2）+ `monitor`（功能1）

### 1.3 非目标

- 实时 tick 级新闻流（用定时轮询，非长连接）
- 推送渠道富交互（MVP：webhook 企微/钉钉机器人 + 文件；不做 APP/邮件富文本）
- 跨市场新闻（Phase 4 仍 A 股相关事件；多市场留 Phase 5）
- 事件预测（只做事件抽取+前瞻映射，不预测事件发生）
- 自动交易执行（YAGNI，总计划 §1.3）

---

## 2. 双功能架构

```
┌─────────────────────────────────────────────────────────────┐
│  共享层   NewsAdapter(采集) → EventExtractor(抽取+打分)      │
│           → EventDedup(去重) → CandidateEvent 队列           │
├─────────────────────────────────────────────────────────────┤
│  功能2 历史建库（复盘导向，优先实现）                        │
│    HistoricalEventMiner（日期范围 → LLM列候选 + 用户审核）   │
│       → BatchReplayRunner（批量 replay + extract-pattern）   │
│       → Replay 库 + Pattern 库                              │
├─────────────────────────────────────────────────────────────┤
│  功能1 监测推送（前瞻导向）                                  │
│    APScheduler 定时 → NewsAdapter → EventExtractor           │
│       → 高分事件 → ForwardOrchestrator.forward              │
│       → PushChannel（webhook/文件）                          │
└─────────────────────────────────────────────────────────────┘
```

| 功能 | 触发 | 事件源 | 产出 | 优先级 |
|---|---|---|---|---|
| **功能2 建库** | 用户指定日期范围 | LLM 知识 + 用户审核 | Replay + Pattern 入库 | **先做** |
| **功能1 推送** | APScheduler 定时 | 实时新闻（NewsAdapter） | 前瞻报告 + 推送 | 后做 |

> 功能2优先：规律库空（0 Pattern）时，功能1推送的都是 qualitative 空洞预判。先建库让 Phase 3 有米下锅，功能1才有质量。

---

## 3. 数据模型

```python
class NewsItem(BaseModel):
    """新闻原文（NewsAdapter 产出）。"""
    id: str
    title: str
    content: str
    published_at: datetime
    source: str  # RSS/MCP/API 名称
    url: str | None = None

class CandidateEvent(BaseModel):
    """抽取的事件 + 打分（EventExtractor 产出，两功能共享）。"""
    event: Event  # 结构化事件（复用 Phase 1 Event）
    significance: int  # 1-4 重大性（4系统级/3主线级/2行业级/1主题噪音）
    a_share_relevance: int  # 1-5 A股标的可达性
    source: str  # "news" | "llm_knowledge" | "calendar"
    dedup_key: str  # 去重键（事件类型+日期+关键实体 hash）
    news_id: str | None = None  # 来源新闻（功能1）
    status: str = "pending"  # pending/replayed/pushed/skipped

class MiningJob(BaseModel):
    """功能2建库任务（HistoricalEventMiner + BatchReplayRunner 状态）。"""
    id: str
    start_date: date
    end_date: date
    filters: dict  # {industry, shock_dimension, impact_direction, top_n}
    candidates: list[CandidateEvent] = []
    progress: dict  # {total, replayed, failed, skipped}
    created_at: datetime
```

> 【2026-10-19 回写：实际事件打分为 5 维——`significance` / `a_share` / `surprise_timing` / `surprise_magnitude` / `impact_scale`；§3/§4.2 的 2 维为原设计口径，后 3 维为设计外新增，见文末】

---

## 4. 新增组件

### 4.1 NewsAdapter（功能1新闻采集）

- **职责**：拉取财经新闻/投研帖 → NewsItem[]
- **源（spike 确认 2026-07-04）**：
  - `stock_news_main_cx`：财新主流财经新闻，100 条实时（功能1实时主源）
  - `news_cctv(date)`：央视新闻联播按日查（功能1当日 + 功能2历史辅助）
  - **韭研公社**（www.jiuyangongshe.com）：投研帖 SSR，requests + HTML 解析可拿（含标题/摘要/关联标的/时间；质量高于纯新闻——已是"事件+产业链+标的"结构化内容）；事件日历 API（`app.jiuyangongshe.com/jystock-app/api/v1/timeline/news`）需鉴权（errCode=9，带 cookie 亦被拒），MVP 不接入，后置逆向
  - `news_economic_baidu`：不可用（Baidu cookies 失败）
- **接口**：`fetch_latest() -> list[NewsItem]`（多源合并：stock_news_main_cx + 韭研投研帖）+ `fetch_by_date(date) -> list[NewsItem]`（news_cctv）
- **降级**：单源失败 → 跳过 + 日志
- **合规**：韭研公社抓取遵守 robots.txt + 服务条款，仅研究用途，标注来源

### 4.2 EventExtractor（共享 LLM 节点）

- **输入**：NewsItem（功能1）或候选事件文本（功能2 LLM 知识）
- **LLM 输出 schema**：
```python
class _EventExtractOut(BaseModel):
    is_event: bool  # 是否构成"重大事件"（过滤噪声）
    event_type: str
    shock_dimension: str
    impact_direction: str
    description: str  # 标准化事件描述
    announcement_date: str  # YYYY-MM-DD
    reaction_date: str | None = None
    key_entities: list[str] = []
    industries: list[str] = []
    significance: int  # 1-4
    a_share_relevance: int  # 1-5
    rationale: str  # 打分理由
```
- **system_prompt 要点**：
  - "你是 A 股事件分析师。判断给定信息是否构成值得复盘/前瞻的重大事件。"
  - "重大性打分（1-4）：4=系统级（冲击全市场，如联储议息/关税战/疫情/重大地缘）；3=主线级（引领市场主线、多板块传导，如芯片管制/AI算力/创新药出海）；2=行业级（单一产业链冲击）；1=主题/噪音（短期概念炒作或个股无传导）"
  - "A股可达性：是否有明确可映射的 A 股标的（5=直接受益/受损明确；1=无关）"
  - "过滤：日常波动/个股财报/无产业链传导的纯宏观不构成事件"
- **post_process**：→ CandidateEvent（复用 Phase 1 Event 构造 + dedup_key = hash(事件类型+日期+关键实体)）

### 4.3 EventDedup（共享去重）

- **职责**：按 dedup_key 去重（同事件多源/多次出现只保留一个）
- **跨功能**：功能2 已 replay 的事件不再 replay；功能1 已推送的事件不再推送
- **持久化**：dedup_key 存 SQLite（`seen_events` 表），跨进程/跨运行

### 4.4 HistoricalEventMiner（功能2事件源）

- **输入**：日期范围 + 过滤（industry / shock_dimension / top_n）
- **逻辑**：
  1. LLM 知识列出该日期范围内的候选重大 A 股相关事件（每个含描述+日期+行业+冲击维度+影响方向+重大性预估），significance ≥ 2（行业级+，过滤主题/噪音）
  2. 多轮分批（如按年/按行业）避免 LLM 一次列太多遗漏
  4. 冲击维度多样性筛选：保证 供给/需求/政策/流动性/情绪 都有覆盖，避免同类扎堆
  5. 辅助源：可调 `news_cctv(date)` 拉日期范围内新闻联播（重大政策事件），LLM 抽取补充候选
  6. 文件审核：候选写到 `mining_candidates_{job_id}.json`（含事件/日期/行业/打分），用户编辑（勾选/修正日期/删减）后跑 batch-replay
- **LLM 输出 schema**：复用 `_EventExtractOut`（source="llm_knowledge"）
- **输出**：CandidateEvent[]（写文件待审核）

### 4.5 BatchReplayRunner（功能2批量建库）

- **输入**：CandidateEvent[]（审核后）
- **逻辑**：
  1. 逐个（串行 MVP）跑 `ReplayOrchestrator.replay` → 入 Replay 库
  2. 跑 `PatternService.extract_pattern` → 入 Pattern 库
  3. 断点续跑：复用 Phase 1 `run_cached`（已 replay 的跳过，按 dedup_key/status 判定）
  4. 进度落盘 + 实时打印（total/replayed/failed/skipped）
  5. 失败不中断：单个 replay 失败记 failed + 继续（LLM 失败/行情不可达等）
- **限流**：每个 replay 间可选 sleep（避免网关频控）
- **成本估算**：N 事件 × replay(7 LLM 节点 + 行情 + 度量)。用户在 mine-events 时给 --top N 控制规模

### 4.6 MonitoringPusher（功能1定时监测）

- **职责**：APScheduler 定时任务 → 采集 → 抽取 → 高分 forward → 推送【2026-10-19 回写：实际未用 APScheduler，改为 `while True + sleep` 单进程循环（`pipeline/monitoring_pusher.py:22-41`），见文末】
- **逻辑**：
  1. 定时（如 30min）调 NewsAdapter.fetch_latest
  2. EventExtractor 抽取 + 打分
  3. EventDedup 过滤已推送
  4. significance ≥ 3（主线级+）+ a_share_relevance ≥ 4 → ForwardOrchestrator.forward
  5. PushChannel 推送前瞻报告
- **去重**：已推送事件不重复（seen_events 表）
- **失败兜底**：forward_mapping 失败（Phase 3 已有 banner）仍推送（报告含失败提示）

### 4.7 PushChannel（推送渠道抽象）

```python
class PushChannel(Protocol):
    def push(self, *, title: str, markdown: str, event: CandidateEvent) -> bool: ...
```
- **WebhookChannel**：POST 到企微/钉钉机器人 webhook（markdown 消息）【2026-10-19 回写：实际实现另有飞书 webhook，渠道为企微/钉钉/飞书 + 本地文件，见文末】
- **FileChannel**：写本地文件（reports/monitoring/）
- **MVP**：这两个渠道；后续可加邮件/APP

---

## 5. CLI

```bash
# 功能2：历史建库（文件审核，两步）
# 步骤1：挖掘候选 → 写文件
eventreplay mine-events --start 2022-01-01 --end 2024-12-31 \
    --industry 半导体 --top 20
# 输出 mining_candidates_{job_id}.json（含事件/日期/行业/打分，用户编辑）

# 步骤2：用户编辑文件后，批量 replay + extract
eventreplay batch-replay --job-file mining_candidates_{job_id}.json
# 串行 replay + 断点续跑 + 进度打印

# 功能1：监测推送
eventreplay monitor --interval 30m --push-webhook https://... \
    --significance 3 --a-share 4
# APScheduler 定时跑；Ctrl-C 停止
```

> 【2026-10-19 回写：`--interval` 实际只接受整数分钟（如 `--interval 30`），不支持 `30m` 写法；另新增 `--source` / `--screen` / `--impact` / `--surprise` 筛选参数，见文末】

---

## 6. 复用 vs 新增

| 组件 | 来源 | 改动 |
|---|---|---|
| ReplayOrchestrator | Phase 1 复用 | 无 |
| PatternService.extract_pattern | Phase 2 复用 | 无 |
| ForwardOrchestrator | Phase 3 复用 | 无 |
| Event / StockMaster / QuoteAdapter | Phase 1 复用 | 无 |
| run_cached / 断点续跑 | Phase 1 复用 | 无 |
| **NewsAdapter** | **新增** | 新闻采集（源待 spike） |
| **EventExtractor** | **新增** | LLM 抽取+打分（共享） |
| **EventDedup** | **新增** | 去重（seen_events 表） |
| **HistoricalEventMiner** | **新增** | LLM 知识列候选 + 审核 |
| **BatchReplayRunner** | **新增** | 批量 replay+extract |
| **MonitoringPusher** | **新增** | 定时监测+推送 |
| **PushChannel** | **新增** | webhook+文件 |

---

## 7. 测试策略

| 层 | 方法 |
|---|---|
| EventExtractor | mock NewsItem → 断言 is_event/significance/a_share_relevance + Event 结构化 |
| EventDedup | 同 dedup_key 多次 → 只保留一个；跨运行持久化 |
| HistoricalEventMiner | mock Reasoner 返回候选列表 → 断言多样性筛选 + 日期范围过滤 |
| BatchReplayRunner | mock ReplayOrchestrator + PatternService → 断言批量入库 + 断点续跑（已 replay 跳过）+ 失败不中断 |
| MonitoringPusher | mock NewsAdapter + ForwardOrchestrator → 断言高分触发 forward + 推送 + 去重 |
| PushChannel | mock webhook（requests）→ 断言 markdown 推送 + 失败重试 |
| 端到端功能2 | mock Reasoner + 真实 Storage → 批量建库 → 规律库有 N 个 Pattern |
| 端到端功能1 | mock NewsAdapter + ForwardOrchestrator → 监测 → forward → 推送文件产出 |

---

## 8. 非功能与风险

| 项 | 处理 |
|---|---|
| 批量建库成本 | --top N 控制规模；串行 + 断点续跑；成本预估打印（N × 单 replay 耗时） |
| LLM 知识日期不准 | 用户审核修正日期；replay 以实际公告日为准（可调） |
| 事件多样性 | 冲击维度/行业覆盖筛选，避免同类扎堆 |
| 新闻源不稳定（功能1） | 多源降级 + 缓存；MVP 可降级为 LLM 知识近期事件 |
| 重复 replay/推送 | seen_events 表（dedup_key）跨运行持久 |
| forward_mapping 超时（Phase 3 遗留） | 功能1推送时 forward_mapping 可能慢/失败；报告含 banner，推送不中断 |
| 网关频控 | 批量 replay 间 sleep；推送渠道失败重试 |
| 规律库质量 | 建库后可跑 find_similar 验证检索质量；低质 Pattern 可人工剔除 |

---

## 9. 决策日志

- **双功能共享采集层**：用户裁定；EventExtractor/EventDedup 共享，两个出口独立
- **功能2优先于功能1**：用户裁定 + 工程判断；规律库空是 Phase 3 瓶颈，先建库让前瞻可用
- **功能2事件源用 LLM 知识 + 用户审核**：用户裁定；不依赖不稳定的历史新闻归档；LLM 列候选 + 用户确认日期
- **批量 replay 串行 MVP**：工程判断；并发复杂（LLM + 行情 + 网关频控），串行 + 断点续跑先跑通，后续可并发
- **功能1新闻源待 spike**：akshare 财经新闻/RSS 可用性待验证；MVP 可降级 LLM 知识近期事件
- **推送渠道 MVP webhook+文件**：YAGNI；企微/钉钉机器人用 webhook 即可，不做富交互
- **事件打分双维度（重大性+A股可达性）**：工程判断；单一维度不够，重大但无 A 股标的的事件（如纯海外宏观）不值得 replay

---

## 实现期状态回写（2026-10-19）

> 本节为 Phase 4 实现完成后的回写；上文为 2026-07-04 的设计历史，未改写。

- **落地情况**：功能2（`mine-events` 挖掘候选 → 用户审核 → `batch-replay` 批量 replay + extract-pattern）与功能1（`monitor` 定时采集 → 抽取打分 → 高分 `forward` → 推送）均已实现；完整 CLI 为 8 子命令（含 `refresh-sw` 等配套命令）。本地库已累计 `events` 61、`seen_events` 107（跑前 83）、`skip_log` 673、`pipeline_runs` 531（跑前 525）；`reports/monitoring/` 下已产出 12 份监测报告（跑前 10）。
- **真实链路实跑（2026-10-19）**：`monitor --source multi` 两轮——默认阈值轮采集 51→初筛 25→识别 13 事件、0 触发；放宽阈值轮（`--surprise 0 --impact 3`）采集 66→初筛 28→识别 9 事件、触发 2 条（强 Pattern 指导，各 3 受益/3 受损 + 预期 CAR 区间），归档 2 份 monitoring 报告 + 2 份根目录前瞻报告；飞书推送以 `--feishu-webhook=` 关闭。
- **事件打分实际为 5 维（超出原设计 2 维）**：实际实现 `significance` / `a_share` / `surprise_timing` / `surprise_magnitude` / `impact_scale` 五维；原 §3/§4.2 只设计重大性 + A股可达性两维，另 3 维（超预期时点 / 超预期幅度 / 影响规模）为设计外扩展的细分打分。
- **推送渠道实际含飞书**：原 §4.7 设计为企微/钉钉 webhook + 本地文件；实际实现企微/钉钉 webhook、**飞书 webhook**、本地文件并存。
- **`news_screening` 两级漏斗（设计外新增）**：新闻采集后先经两级初筛（规则级 → 事件抽取级）再进 `EventExtractor`，用于在 LLM 打分前压低噪声与成本；该模块不在原设计中。
- **CLI 参数实际增补**：`--source` / `--screen` / `--impact` / `--surprise` 为原设计未列出的筛选参数；`--interval` **只接受整数分钟**（如 `--interval 30`），不支持上文示例的 `30m` 写法。
- **定时调度未用 APScheduler**：原设计与 §7 技术栈写 APScheduler；实际实现为 `run_monitor_loop` 的 `while True + sleep` 单进程循环（`src/eventreplay/pipeline/monitoring_pusher.py:22-41`），`--interval` 决定 sleep 分钟数，`pyproject.toml` 未引入 APScheduler 依赖。
- **“待 spike”与“spike 已确认”并存的澄清**：关键决策 ④ 与 §9 决策日志仍写“功能1新闻源待 spike”，而 §4.1 写“spike 确认 2026-07-04”，二者系文档迭代历史。实际新闻源为 **akshare（含 cctv）+ 财联社（cls）+ 金十（jin10）+ 韭研公社（jiuyan）融合**；§4.1 中韭研事件日历 API 鉴权受限、`news_economic_baidu` 不可用等结论仍成立，MVP 未接入事件日历。
