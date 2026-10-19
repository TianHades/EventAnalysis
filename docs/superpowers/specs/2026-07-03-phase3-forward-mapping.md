# Phase 3 Spec：前瞻映射（Forward Mapping）

- **状态**：已实现（2026-10-19 状态回写；设计与实现的偏离见文末）
- **日期**：2026-07-03
- **所属总计划**：[2026-06-30-event-replay-master-plan.md](./2026-06-30-event-replay-master-plan.md) 的 Phase 3
- **依赖**：Phase 1（复用 EventIngest / IndustryChain / CandidateGen / ReportAssembly / StockMaster / QuoteAdapter）；Phase 2（复用 PatternService.find_similar / PatternStore）
- **关键决策**：① 两种模式（盘前纯 Pattern + 盘后校准）；② LLM 综合多 Pattern；③ 三层降级（强 Pattern → 弱 Pattern → 纯定性）；④ 方案 A（最小新增 + 最大复用）

---

## 1. 目标与范围

### 1.1 目标

新事件发生时，检索 Phase 2 规律库找相似历史 Pattern，LLM 综合预判受益/受损标的 + 预期 CAR 区间 + 传导逻辑，产出前瞻预判报告。从"后视复盘"跃迁到"前瞻预判"。

### 1.2 范围

- ✅ **ForwardOrchestrator**：路由决策（三层降级）+ 串联节点
- ✅ **ForwardMappingNode**（LLM 新增）：综合多 Pattern → 预判标的 + 预期区间 + 传导逻辑
- ✅ **InitialCalibrationNode**（量化新增，仅 post 模式）：实际 [+1]/[+5] CAR vs 预期 → 确认/背离
- ✅ **ForwardReport**（适配 ReportNode prompt）：前瞻报告
- ✅ **ForwardResult / ForwardPrediction** schema
- ✅ CLI `eventreplay forward`
- ✅ 三层降级逻辑

### 1.3 非目标

- Phase 4 自动监测推送（留后续）
- 实时行情接入（Phase 3 用日线，非 tick）
- 多 Pattern 统计聚合（均值/方差跨 Pattern 计算）—— LLM 综合即可，不做统计模型

---

## 2. 三层降级（核心路由逻辑）

```
EventIngest (always) → FindSimilar (always)
                            ↓
                    检查 top-K 结果
                            ↓
    ┌──────────────────┬──────────────────┬──────────────────┐
    │ max_score > 0.6  │ 0.3 < max < 0.6  │ max < 0.3 / 空   │
    │ 强 Pattern 模式   │ 弱 Pattern 模式   │ 纯定性模式        │
    ├──────────────────┼──────────────────┼──────────────────┤
    │ ForwardMapping   │ ForwardMapping   │ IndustryChain    │
    │ (Pattern 指导)    │ (弱参考)          │ + CandidateGen   │
    │ + 预期 CAR 区间   │ + 宽预期区间       │ (Phase 1 复用)   │
    │ + 传导逻辑        │ + 传导逻辑        │ 无预期 CAR        │
    ├──────────────────┼──────────────────┼──────────────────┤
    │ [post] 校准       │ 不校准(太不确定)  │ 不校准            │
    │ ForwardReport    │ ForwardReport    │ ForwardReport     │
    │ (定量前瞻)        │ (弱定量前瞻)      │ (纯定性前瞻)      │
    └──────────────────┴──────────────────┴──────────────────┘
```

| 层 | 触发条件 | 产出 | 置信度 |
|---|---|---|---|
| **强 Pattern** | top-1 cosine > 0.6 | 预判标的 + 预期 CAR 区间 + 传导逻辑 + 来源 Pattern 溯源 | 高 |
| **弱 Pattern** | 0.3 < top-1 ≤ 0.6 | 同上但区间更宽 + 标注"弱参考" | 中 |
| **纯定性** | top-1 < 0.3 或规律库空 | EventIngest → IndustryChain → CandidateGen → 纯定性报告（无 CAR 区间） | 低 |

> 报告永远产出。降级只影响"有没有预期 CAR 区间"和"置信度标注"。

---

## 3. 数据模型

```python
class ForwardPrediction(BaseModel):
    stock_id: str
    stock_name: str | None = None
    direction: StockDirection  # 受益/受损
    expected_car_0_20_range: str | None = None  # "10%~30%"（纯定性模式为 None）
    expected_car_0_60_range: str | None = None
    rationale: str
    source_pattern_ids: list[str] = []  # 哪些 Pattern 预测了它
    # 盘后校准（仅 post 模式 + 强 Pattern 层）
    actual_car_0_1: float | None = None
    actual_car_0_5: float | None = None
    calibration: str | None = None  # confirming / diverging / neutral / None

class ForwardResult(BaseModel):
    event_id: str
    event: Event  # 结构化事件
    mode: str  # "pre" | "post"
    degradation_level: str  # "strong" | "weak" | "qualitative"
    matched_pattern_ids: list[str] = []
    matched_scores: list[float] = []
    transmission_logic: str = ""  # 综合传导逻辑
    beneficiary_predictions: list[ForwardPrediction] = []
    victim_predictions: list[ForwardPrediction] = []
    confidence: str = ""  # 高/中/低
    key_risks: str = ""  # 预期差风险
    # 纯定性模式额外数据
    chain_nodes: list[ChainNode] = []  # IndustryChain 产出（纯定性模式）
```

---

## 4. 新增组件

### 4.1 ForwardMappingNode（LLM 节点）

- **输入**：`Event` + top-K `Pattern[]` + `degradation_level`（strong/weak）
- **LLM 输出 schema**：
```python
class _PredictedStockOut(BaseModel):
    code: str
    name: str
    direction: str
    expected_car_0_20: str  # "10%~30%"
    expected_car_0_60: str
    rationale: str

class _ForwardMappingOut(BaseModel):
    transmission_logic: str
    beneficiary_predictions: list[_PredictedStockOut]
    victim_predictions: list[_PredictedStockOut]
    confidence: str  # 高/中/低
    key_risks: str
```
- **system_prompt 要点**：
  - "你是前瞻映射分析师。基于新事件 + N 个相似历史 Pattern（每个含签名、传导模板、典型受益/受损标的及其历史 CAR），综合预判本次事件可能受益/受损的 A 股标的。"
  - "预期 CAR 区间：参考历史 Pattern 的 observed_car 范围，结合本次事件的特异性调整。弱参考模式给更宽区间。"
  - "标的须为真实 A 股（系统会反查）。传导逻辑须有因果推导。"
- **post_process**：resolve 每只标的经 StockMaster 反查 → ForwardPrediction[]

### 4.2 InitialCalibrationNode（量化节点，仅 post 模式 + strong 层）

- **输入**：`ForwardPrediction[]` + `Event`（reaction_date）
- **逻辑**：
  - 对每只预测标的：`QuoteAdapter.daily_adjusted` 拉 sina 行情 → `measure_stock` 算 CAR[0,+1]/[0,+5]
  - 解析预期区间 `expected_car_0_20_range`（如 "10%~30%"→ lo=0.10, hi=0.30）
  - 比对实际 vs 预期：
    - `actual_5 > 0` 且在预期方向 → `confirming`
    - `actual_5` 与预期方向相反 → `diverging`
    - 数据不足 → `neutral`
  - 设 `actual_car_0_1`, `actual_car_0_5`, `calibration`
- **跳过条件**：pre 模式 / weak 或 qualitative 层 / 无行情数据

### 4.3 ForwardOrchestrator

```python
class ForwardOrchestrator:
    def forward(self, *, description, date, mode="pre", top_k=3):
        # 1. EventIngest (复用 Phase 1)
        event = self.ingest.run_cached(...)

        # 2. FindSimilar (复用 Phase 2)
        patterns_with_scores = self.pattern_service.find_similar(
            event.description, top_k=top_k
        )

        # 3. 判断降级层
        max_score = patterns_with_scores[0][1] if patterns_with_scores else 0
        if max_score > 0.6:
            level = "strong"
        elif max_score > 0.3:
            level = "weak"
        else:
            level = "qualitative"

        # 4. 路由
        if level in ("strong", "weak"):
            # Pattern 指导路径
            result = self._forward_with_patterns(event, patterns_with_scores, level, mode)
        else:
            # 纯定性路径（复用 Phase 1 IndustryChain + CandidateGen）
            result = self._forward_qualitative(event, mode)

        # 5. 报告
        markdown = self.report_node.run(...)
        return markdown, path
```

### 4.4 ForwardReport（适配 ReportNode）

- **system_prompt 改为前瞻口径**：
  - "你是前瞻预判报告撰写者。基于新事件 + 相似历史 Pattern 类比，产出前瞻预判报告。"
  - 三层不同 prompt 模板（strong/weak/qualitative）
  - post 模式加校准列
- **报告结构**：
```
# <事件> 前瞻预判报告
## 1 事件摘要 + 置信度（高/中/低）
## 2 相似历史 Pattern（top-K + 相似度 + 传导模板摘要）
## 3 预判受益/受损标的
  | 代码 | 名称 | 方向 | 预期CAR[+20] | 预期CAR[+60] | 逻辑 | 来源Pattern |
  （post 模式加：| 实际CAR[+1] | 实际CAR[+5] | 校准 |）
## 4 综合传导逻辑
## 5 关键风险与预期差
## 6 局限（前瞻≠确定；历史不一定重演；Pattern 样本 N 个）
```

**修订（2026-09-06）：报告结构新增 §1.5 事件定性预判（五维）**

- 动机：原设计中五维定性（程度/范围/持续/显著/超预期）只存在于事后 PatternQualitative，前瞻报告仅引用历史 Pattern 的定性做类比，对**当事事件**没有事前定性落点，事后也无定性对账闭环。
- 新增 `QualitativeForecastNode`（`nodes/qualitative_forecast.py`），三层分工：
  - **证据层**（程序）：`collect_anchors`——事件前 20 交易日基准/当事标的收益与超额（surprise 的"已定价程度"代理）+ 相似 Pattern 实测五维众数；全容错。
  - **判定层**（LLM）：带锚点综合五维 + **逐维置信度**（高/中/低）；锚点冲突须在 rationale 写取舍；LLM 失败→先例众数兜底（`source="fallback"`，报告打 banner）。
  - **校准层**（程序）：`calibrate_qualitative`（`nodes/calibration.py`）——事后与 extract_pattern 实测 PatternQualitative 逐维严格相等对账 + hit_rate。【2026-10-19 回写：该节点已实现且有单测，但无调用方，未接入 forward 主链路——盘后定性对账当前不会自动触发，见文末】
- 报告章节编号取 **§1.5**（而非重排 1-6），保持本节既有章节号与外部引用稳定。
- 五维档位/维度枚举单一事实源：`schema/pattern.py` 模块级 `QUALITATIVE_LABELS`（PatternQualitative / QualitativeForecast / 对账维度共用）。

---

## 5. CLI

```bash
# 盘前前瞻（默认）
eventreplay forward --desc "美国对华芯片管制升级" --date 2026-07-15

# 盘后校准
eventreplay forward --desc "..." --date 2026-07-15 --mode post --top-k 3
```

---

## 6. 复用 vs 新增

| 组件 | 来源 | 改动 |
|---|---|---|
| EventIngest | Phase 1 复用 | 无 |
| PatternService.find_similar | Phase 2 复用 | 无 |
| IndustryChain / CandidateGen | Phase 1 复用（仅 qualitative 路径） | 无 |
| StockMaster / QuoteAdapter | Phase 1 复用 | 无 |
| **ForwardMappingNode** | **新增** | LLM 节点（Pattern 综合） |
| **InitialCalibrationNode** | **新增** | 量化（post 模式实际 CAR） |
| **ForwardOrchestrator** | **新增** | 路由 + 降级 |
| **ForwardReport** | 适配 ReportNode | 改 prompt（前瞻口径 + 三层模板） |
| **ForwardResult / ForwardPrediction** | **新增** | schema |

---

## 7. 测试策略

| 层 | 方法 |
|---|---|
| ForwardMapping | mock Reasoner + mock Patterns（含/不含预期区间）→ 断言预判列表 + resolve 反查 |
| InitialCalibration | 合成行情 → 断言 confirming/diverging/neutral 判定 + 区间解析 |
| ForwardOrchestrator 路由 | mock find_similar 返回高/低/空 → 断言走 strong/weak/qualitative 路径 |
| 端到端 pre + strong | 真实 PatternService（需 Phase 2 先 extract-pattern）→ 前瞻报告含预期 CAR 区间 |
| 端到端 qualitative | 空规律库 → 复用 IndustryChain + CandidateGen → 纯定性报告 |
| 端到端 post | + 真实 sina 行情 → 校准列有值 |

---

## 8. 非功能与风险

| 项 | 处理 |
|---|---|
| 前瞻≠确定 | 报告固定标注"前瞻预判，历史不一定重演"；预期区间是参考非保证 |
| Pattern 样本少 | 置信度标注反映；qualitative 层明确无定量预期 |
| LLM 幻觉 | 预判标的经 StockMaster 反查（同 Phase 1 护栏） |
| 盘后校准窗口短 | [+1]/[+5] 只是初始信号，不等于最终验证；标注"初始校准" |
| 新事件无历史类比 | 三层降级保底：永远产出报告，qualitative 层仍有价值 |

---

## 9. 决策日志

- **两种模式**（盘前+盘后）：用户裁定；覆盖"纯前瞻"到"前瞻+初始验证"场景
- **LLM 综合多 Pattern**：用户裁定；比并集更灵活，能处理多 Pattern 传导逻辑的矛盾/互补
- **三层降级**：用户裁定；保证系统在规律库不成熟时仍有产出
- **方案 A（最小新增）**：用户裁定；复用 Phase 1/2 已验证组件，新增 2 节点 + 1 orchestrator

---

## 实现期状态回写（2026-10-19）

> 本节为 Phase 3 实现完成后的回写；上文为 2026-07-03 设计 + 2026-09-06 §1.5 修订，未改写。

- **三层降级路由已实现**：strong / weak / qualitative 三档路由与对应报告模板均已实现，`eventreplay forward` 可用；已产出 **8 份 `fwd_*` 评估报告**（`reports/fwd_*.md`，最新 `reports/fwd_20261001_repo_outright.md`，2026-10-01）；另有 **16 份按日期命名的前瞻预判报告**，其中 2 份由 2026-10-19 monitor 真实链路自动触发（强 Pattern 指导）。
- **五维事前定性 + 盘后对账（§1.5）已实现**：`nodes/qualitative_forecast.py`（证据层 `collect_anchors` / 判定层 LLM + 逐维置信度 / 先例众数兜底 banner）与报告 §1.5 章节已落地；盘后对账逻辑 `calibrate_qualitative`（`nodes/calibration.py`）已实现并有单测。
- **未接线（明确偏离）**：`calibrate_qualitative` **无调用方**，未接入 `forward` 主链路——盘后定性对账结果当前不会自动计算/回写，§1.5 的“事前定性 → 事后对账”闭环需该节点接线后才完整。
