# Phase 2 实施计划：规律库

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
