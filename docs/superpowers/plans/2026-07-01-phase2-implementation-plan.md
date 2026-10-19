# Phase 2 实施计划：规律库

- **状态**：**P1–P6 全部实现**（2026-10-19 状态回写；逐步骤结果见文末 §实现状态）。
- **依据 spec**：[phase2-pattern-library.md](../specs/2026-07-01-phase2-pattern-library.md)
- **方法**：TDD + 增量；复用 Phase 1 基座，纯增量不改动既有逻辑（除 §2 小补丁）

## 步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| **P1 Phase 1 补丁** | Storage 加 `attribution_results` 表 + save/get_attribution；orchestrator.replay 末尾存 | AttributionResult 往返测试 |
| **P2 schema** | `schema/pattern.py`：Pattern + ArchetypalStock | 构造/序列化测试 |
| **P3 Embedder** | `llm/embedder.py`：网关 embedding-3 直连 HTTP；text-hash 缓存；float32；失败→None | 真实网关冒烟(dim 2048) + mock + 缓存命中 |
| **P4 PatternStore** | Storage 加 `patterns` 表（BLOB embedding）；save_pattern(getter)/get_pattern/search(cosine+过滤, top-K)；source_replay_id 唯一 | 合成 embedding cosine 检索 + 幂等 + null embedding |
| **P5 ReplayLoader + PatternExtractor** | ReplayLoader 组装 4 表；PatternExtractor(LLM `_PatternLLM`) → Pattern + 调 Embedder | mock Reasoner 形状 + 真实 LLM 冒烟 |
| **P6 FindSimilar + CLI** | FindSimilarService(embed→search)；CLI extract-pattern / find-similar | mock 端到端检索 |

## 执行顺序
P1 → P2 → P3 → P4 → P5 → P6（P3/P4 可并行，P5 依赖 P2+P3，P6 依赖 P4+P5）

## 实现状态（2026-10-19 回写）

> 2026-10-19 逐步骤复核（原步骤表保留在上方）。全局事实见 [总计划 §10](../specs/2026-06-30-event-replay-master-plan.md) 与 [Phase 2 spec 文末回写](../specs/2026-07-01-phase2-pattern-library.md)。

| 步骤 | 结果 | 落地证据与说明 |
|---|---|---|
| **P1 Phase 1 补丁** | **完成** | `attribution_results` 表（`storage.py:77-80`）+ `save_attribution`（`:229`）/`get_attribution`（`:236`）；`pipeline/orchestrator.py:165` replay 末尾写入；库内 61 行 |
| **P2 schema** | **完成（超越）** | `schema/pattern.py:55` `Pattern`、`:21` `ArchetypalStock`；实现期另增五维定性 `PatternQualitative`（`:31-52`：程度/范围/持续/显著/超预期），原设计未含，属实现期扩展 |
| **P3 Embedder** | **完成（偏离原口径）** | `llm/embedder.py:32`；偏离设计「固定网关 embedding-3(2048)」：实际 `EMBEDDING_BACKEND` 默认 auto 双后端（在线智谱 2048 维 / 本地 bge-small-zh-v1.5 512 维，首次选定锁定不切换）；text-hash 缓存、失败→None 均已实现。当前库内 58 条 Pattern 的 embedding 均为本地 512 维（`embedding_model=BAAI/bge-small-zh-v1.5`） |
| **P4 PatternStore** | **完成** | `patterns` 表（`storage.py:81-91`，BLOB embedding、`source_replay_id` 主键=唯一幂等）；`save_pattern`（`:244`）/`get_pattern`（`:266`）/`search_patterns`（`:279`，cosine + 结构化过滤 top-K，null embedding 可检索）；库内 58 行 |
| **P5 ReplayLoader + PatternExtractor** | **完成** | `replay_loader.py:10-25` 按 replay_id 组装 event/chain/attribution/measurements 4 表上下文；`pipeline/nodes/pattern.py:82` PatternExtractor → Pattern + 调 Embedder；库内 58 Pattern 的 `created_at` 均为 2026-08-26。注意 `replays` 表 0 行：loader 从 4 张表重建上下文，不依赖 `replays` 表 |
| **P6 FindSimilar + CLI** | **完成（超越）** | `pattern_service.py:31-54` `find_similar`；CLI `extract-pattern`（`cli.py:122`）/ `find-similar`（`cli.py:144`）；检索新增 `degraded` 语义（embed 失败→纯结构化过滤并明示「检索不可用」），超出计划 |

### 现状与缺口

- Pattern 年份分布（58 个）：2016:10 / 2017:7 / 2018:3 / 2019:4 / 2020:4 / 2021:6 / 2022:7 / 2023:8 / 2024:4 / 2025:5；61 个 events 中 3 个无 Pattern（`china_japan_dualuse_2026`、`hk_tencent_dump`、`india_pakistan_2025`）。
- 全部 58 个 Pattern 均含五维定性与 embedding（`embedding_model=BAAI/bge-small-zh-v1.5`）。
- 无步骤未做；Pattern 聚类合并、跨 Replay 区间聚合、`sqlite-vec` 向量库维持原非目标（未做），检索仍为 SQLite + BLOB + 暴力 cosine。
