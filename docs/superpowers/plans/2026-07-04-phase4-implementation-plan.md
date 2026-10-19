# Phase 4 实施计划：事件采集与监测推送

- **状态**：**P1–P5 全部实现**（2026-10-19 状态回写；逐步骤结果见文末 §实现状态）。
- **依据 spec**：[phase4-monitoring-mining.md](../specs/2026-07-04-phase4-monitoring-mining.md)
- **方法**：TDD + 增量；复用 Phase 1/2/3 组件，新增最小代码
- **新闻源**：spike 确认（2026-07-04）`stock_news_main_cx`（实时）+ `news_cctv(date)`（按日）可用；`news_economic_baidu` 不可用

## 步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| **P1 共享层** | `schema/news.py`（NewsItem/CandidateEvent/MiningJob）+ `pipeline/nodes/event_extractor.py`（LLM 抽取+打分）+ `pipeline/event_dedup.py`（seen_events 去重） | mock NewsItem → 断言 is_event/significance/a_share_relevance + Event 结构化；同 dedup_key 去重 + 跨运行持久 |
| **P2 NewsAdapter** | `adapters/news_adapter.py`（fetch_latest=stock_news_main_cx + fetch_by_date=news_cctv） | mock akshare → NewsItem[]；真实 smoke 两接口可达 |
| **P3 功能2挖掘** | `pipeline/historical_miner.py`（LLM 列候选 + 多样性筛选 + 文件输出）+ CLI `mine-events` | mock Reasoner → 候选列表 + 冲击维度多样性 + 日期范围 + 写 `mining_candidates_{job_id}.json` |
| **P4 功能2建库** | `pipeline/batch_replay.py`（批量 replay+extract + 断点续跑 + 进度 + 失败不中断）+ CLI `batch-replay` | mock ReplayOrchestrator+PatternService → 批量入库 + 已 replay 跳过 + 失败不中断；端到端 mock Reasoner+真实 Storage → 规律库有 N Pattern |
| **P5 功能1推送** | `pipeline/push_channel.py`（WebhookChannel+FileChannel）+ `pipeline/monitoring_pusher.py`（APScheduler 定时→采集→抽取→高分 forward→推送+去重）+ CLI `monitor` | mock NewsAdapter+ForwardOrchestrator → 高分触发 forward + 推送 + 去重；webhook mock requests |

## 执行顺序

P1（共享层）→ P2（NewsAdapter）→ P3（功能2挖掘）→ P4（功能2建库）→ P5（功能1推送）

- P1 共享层：EventExtractor/EventDedup 两功能都用，先做
- P2-P4 功能2：建库优先（规律库空是 Phase 3 瓶颈）
- P5 功能1：推送后做（依赖 P1+P2+Phase 3 ForwardOrchestrator）

## 关键复用

- Phase 1：ReplayOrchestrator.replay / run_cached（断点续跑）/ QuoteAdapter / StockMaster
- Phase 2：PatternService.extract_pattern
- Phase 3：ForwardOrchestrator.forward（含拆分+重试+失败 banner）
- Phase 1：Event schema / LLMNode 基类 / Storage

## 测试要点

- EventExtractor：契约测试（mock Reasoner 按 _EventExtractOut 分发）+ 过滤噪声新闻（is_event=false）
- EventDedup：跨运行持久（tmp_path Storage）+ 同 dedup_key 幂等
- HistoricalEventMiner：多样性筛选（冲击维度覆盖）+ 日期范围过滤 + 文件审核格式
- BatchReplayRunner：断点续跑（已 replay 跳过）+ 失败不中断（单事件失败记 failed 继续）+ 进度
- MonitoringPusher：去重（已推送不重复）+ 阈值过滤（significance/a_share_relevance）+ 推送失败重试
- 端到端功能2：mock Reasoner + 真实 Storage → N 事件 → 规律库 N Pattern → find_similar 可检索

## 实现状态（2026-10-19 回写）

> 2026-10-19 逐步骤复核（原步骤表保留在上方）。全局事实见 [总计划 §10](../specs/2026-06-30-event-replay-master-plan.md) 与 [Phase 4 spec 文末回写](../specs/2026-07-04-phase4-monitoring-mining.md)。

| 步骤 | 结果 | 落地证据与说明 |
|---|---|---|
| **P1 共享层** | **完成（超越）** | `schema/news.py:12` NewsItem / `:23` CandidateEvent / `:39` MiningJob；`pipeline/nodes/event_extractor.py:46`；`pipeline/event_dedup.py:31`。超越：事件打分实际为 **5 维**（`schema/news.py:27-31`、`event_extractor.py:33-37,107-111`：significance / a_share_relevance / surprise_timing / surprise_magnitude / impact_scale），原计划仅 2 维；另增 `pipeline/nodes/news_screening.py:56` 两级漏斗（设计外新增） |
| **P2 NewsAdapter** | **完成（超越）** | `adapters/news_adapter.py:31` akshare + `:143` cctv；实际另有韭研公社 `:80`、财联社 `:179`、金十 `:224` 及多源融合 `:286`（`MultiSourceNewsAdapter`）；CLI `--source`（`cli.py:307`） |
| **P3 功能2挖掘** | **完成** | `pipeline/nodes/historical_miner.py:160`；CLI `mine-events`（`cli.py:233`）；本次收尾修复阶段1 LLM 失败降级时的 KeyError（`cli.py:254-258`：友好报错而非 KeyError） |
| **P4 功能2建库** | **完成** | `pipeline/batch_replay.py:78`；CLI `batch-replay`（`cli.py:269`）；库内已累计 61 events / 58 patterns，58 个 Pattern 全部含五维定性 + embedding；61 个 events 中 3 个无 Pattern（`china_japan_dualuse_2026`、`hk_tencent_dump`、`india_pakistan_2025`） |
| **P5 功能1推送** | **完成（偏离实现方式）** | `pipeline/monitoring_pusher.py:43` + 定时主循环 `:22-40` `run_monitor_loop`；`pipeline/push_channel.py`（Webhook/File/Feishu）；CLI `monitor`（`cli.py:298`）。偏离：定时用 `while True + sleep` 单进程循环，非计划所述 APScheduler（`pyproject.toml` 未引入该依赖）；`--interval` 仅接受整数分钟，不支持 `30m` 写法；`--source/--screen/--impact/--surprise` 为计划外新增参数 |

### 现状与缺口

- 功能2（`mine-events` → 审核 → `batch-replay`）与功能1（`monitor` 采集→抽取→高分 forward→推送）均已落地；库内 `seen_events` 83、`skip_log` 673，`reports/monitoring/` 已产出 10 份监测报告。
- **缺口**：监测当前固定 A 股（`cli.py:329` 以 `make_adapters("A股")` 装配）；真实新闻源 + 真实 LLM 的端到端监测本轮未复跑。
