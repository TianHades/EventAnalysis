# Phase 3 实施计划：前瞻映射

- **依据 spec**：[phase3-forward-mapping.md](../specs/2026-07-03-phase3-forward-mapping.md)
- **方法**：TDD + 增量；复用 Phase 1/2 组件，新增最小代码

## 步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| **P1 schema + ForwardMapping** | `schema/forward.py`（ForwardPrediction + ForwardResult）+ `pipeline/nodes/forward_mapping.py`（LLM 综合 Pattern） | mock Reasoner + mock Pattern → 断言预判列表 |
| **P2 InitialCalibration** | `pipeline/nodes/calibration.py`（量化：实际 [+1]/[+5] CAR vs 预期区间 → confirming/diverging） | 合成行情 → 断言校准判定 |
| **P3 ForwardOrchestrator** | `pipeline/forward_orchestrator.py`（三层降级路由：strong/weak/qualitative） | mock find_similar → 断言路径选择 |
| **P4 CLI + Report + 集成** | CLI `forward` + ForwardReport prompt + 端到端测试 | mock 全链路 → 前瞻报告产出 |

## 执行顺序
P1 → P2 → P3 → P4
