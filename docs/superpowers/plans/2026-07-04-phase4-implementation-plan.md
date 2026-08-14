# Phase 4 实施计划：事件采集与监测推送

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
