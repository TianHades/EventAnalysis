# Phase 3 实施计划：前瞻映射

- **状态**：**P1–P4 全部实现**（2026-10-19 状态回写；逐步骤结果见文末 §实现状态）。
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

## 实现状态（2026-10-19 回写）

> 2026-10-19 逐步骤复核（原步骤表保留在上方）。全局事实见 [总计划 §10](../specs/2026-06-30-event-replay-master-plan.md) 与 [Phase 3 spec 文末回写](../specs/2026-07-03-phase3-forward-mapping.md)。

| 步骤 | 结果 | 落地证据与说明 |
|---|---|---|
| **P1 schema + ForwardMapping** | **完成（超越）** | `schema/forward.py:24` `ForwardPrediction`、`:86` `ForwardResult`；`pipeline/nodes/forward_mapping.py:76`（LLM 综合 Pattern）。实现期另增五维事前定性预判 `pipeline/nodes/qualitative_forecast.py:122`（2026-09-06 spec §1.5 修订），超出本计划范围 |
| **P2 InitialCalibration** | **完成（偏离）** | 数值 CAR 对账 `pipeline/nodes/calibration.py:63` `InitialCalibrationNode`；五维定性对账 `calibrate_qualitative`（`calibration.py:23`）已实现并有单测（`tests/test_qualitative_forecast.py:164-169`），但**无生产调用方**，未接入 forward 主链路 |
| **P3 ForwardOrchestrator** | **完成** | `pipeline/forward_orchestrator.py:36`；三层降级路由 strong/weak/qualitative（`:104-117`）；检索不传 impact_direction 硬过滤（`:94-100`，2026-09 修复方向口径不一致导致的强匹配误杀） |
| **P4 CLI + Report + 集成** | **完成** | CLI `forward`（`cli.py:199`，含 `--mode pre/post`、`--top-k`、`--market`、`--feishu-webhook`）；前瞻报告命名 `{日期}_{事件描述}.md`；已产出 8 份 `fwd_*.md`，最新 `reports/fwd_20261001_repo_outright.md`（2026-10-01） |

### 现状与缺口

- 三层降级 + 五维事前定性 + Forward 报告均已落地，`eventreplay forward` 可用；已产出 8 份 `fwd_*` 评估报告（另有 14 份按日期命名的前瞻预判报告）。
- **缺口 1（明确偏离）**：`calibrate_qualitative` 无调用方——盘后定性对账当前不会自动计算/回写，「事前定性 → 事后对账」闭环未完成。
- **缺口 2**：真实 LLM 端到端（含真实 forward 全链路）本轮未运行；前瞻报告中的历史样例为既有产物。
