# Phase 2 Spec：规律库（Pattern Library）

- **状态**：已实现（2026-10-19 状态回写；设计与实现的偏离见文末）
- **日期**：2026-07-01
- **所属总计划**：[2026-06-30-event-replay-master-plan.md](./2026-06-30-event-replay-master-plan.md) 的 Phase 2
- **依赖**：Phase 1（复用 Reasoner / Storage / schema；需小补丁持久化 AttributionResult）
- **关键决策**：① Pattern 提炼用 LLM 辅助综合（读 Replay 全产物）；② embedding 用 GLM 网关 `embedding-3`（spike 实跑 dim 2048 ✓）【2026-10-19 回写：实际口径为 auto 双后端——有 `ZHIPU_API_KEY` 且可达则智谱 embedding-3（2048 维），否则本地 bge-small-zh-v1.5（512 维）；首次选定后锁定不切换（`cache/embedder_backend.txt`），见文末】；③ 向量库 MVP 用 SQLite + 暴力 cosine（百级 Pattern 再升 sqlite-vec）；④ 每 Replay → 1 Pattern（不做聚类/合并，YAGNI）

---

## 1. 目标与范围

### 1.1 目标

从 Phase 1 的 Replay 结果提炼可复用 Pattern（事件签名 + 传导模板 + 典型受益/受损标的 + 观察到的幅度/持续性），支持"找相似历史事件"检索，为 Phase 3 前瞻映射铺路。

### 1.2 范围

- ✅ **ReplayLoader**：从 Storage 组装完整 Replay 上下文
- ✅ **PatternExtractor**（LLM 节点）：Replay → 结构化 Pattern
- ✅ **Embedder**（网关 embedding-3）：Pattern 签名 → 向量
- ✅ **PatternStore**（SQLite + 暴力 cosine）：存储 + 检索（top-K + 结构化过滤）
- ✅ **FindSimilarService** + CLI
- ✅ Phase 1 小补丁：持久化 AttributionResult

### 1.3 非目标（后续/增强）

- Pattern 聚类合并 / 跨 Replay 增量更新（样本少 YAGNI；"区间"统计待多 Replay 聚合）
- 前瞻映射本身（Phase 3）
- `sqlite-vec` 向量库（百级 Pattern 再升级）
- embedding 之外的多模态检索

---

## 2. 前置：Phase 1 补丁（持久化 AttributionResult）

Phase 1 Storage 仅存 event/chain/measurements，**未存 AttributionResult**（最终强弱分级在 PoolMember 上）。Phase 2 PatternExtractor 需读取分级结果，故补：

- Storage 加表 `attribution_results(replay_id TEXT PK, event_id TEXT, payload TEXT)`
- 方法 `save_attribution(ar: AttributionResult, replay_id)` / `get_attribution(replay_id) -> AttributionResult | None`
- Phase 1 orchestrator.replay 末尾加 `self.storage.save_attribution(attribution, event_id)`

> 不改动 Phase 1 已有逻辑，纯增量。measurements 已含 direction（Phase 1 设入），tier 在 AttributionResult 中。

---

## 3. Pattern 数据模型

```python
class ArchetypalStock(BaseModel):
    industry_node: str
    stocks: list[str]                       # 代码
    observed_car_0_20: float | None         # 观察到的单值（MVP 不臆造区间）
    observed_car_0_60: float | None
    persistence_note: str | None

class Pattern(BaseModel):
    id: str
    source_replay_id: str                   # UNIQUE（幂等：重跑覆盖）
    created_at: datetime
    # 可追溯
    model_alias: str
    prompt_hash: str
    embedding_model: str | None             # "embedding-3"
    # 签名（检索键）
    shock_dimension: ShockDimension         # 枚举：可靠过滤用
    impact_direction: ImpactDirection       # 枚举：可靠过滤用
    event_type: str                         # 自由词表：仅显示
    industries: list[str]                   # 申万行业名：子串过滤
    event_summary: str                      # LLM 标准摘要：embedding 输入
    embedding: list[float] | None           # dim 2048；null=嵌入失败（仅结构化可检索）
    # 传导模板（LLM 归纳）
    transmission_template: str
    # 典型标的（观察单值，非区间——区间待多 Replay 聚合，后续）
    archetypal_beneficiaries: list[ArchetypalStock]
    archetypal_victims: list[ArchetypalStock]
    evidence_replay_ids: list[str]          # MVP = [source_replay_id]
```

> 与总计划 §2 Pattern 实体一致并具象化；`embedding_text` 字段被 `event_summary` 承担。
>
> 【2026-10-19 回写：实现期 Pattern 另含五维定性 `PatternQualitative`（程度/范围/持续/显著/超预期），原 §3 未含该字段，属实现期扩展补记；见文末】

---

## 4. 架构与组件

```
┌──────────────────────────────────────────────────────────────┐
│  CLI: extract-pattern --replay-id / find-similar --desc      │
├──────────────────────────────────────────────────────────────┤
│  PatternExtractor*(LLM, _PatternLLM schema)                  │
│       ↑ ReplayLoader(storage 4 表 → Replay 上下文 JSON)       │
│  Embedder#(网关 embedding-3, BLOB, text-hash 缓存, 失败→null) │
│  FindSimilarService(Embedder + PatternStore)                 │
├──────────────────────────────────────────────────────────────┤
│  PatternStore(SQLite patterns 表, BLOB, 暴力 cosine + 过滤)   │
├──────────────────────────────────────────────────────────────┤
│  复用 Phase 1: Storage(+attribution) / Reasoner / schema     │
└──────────────────────────────────────────────────────────────┘
 (* = Claude 推理节点, # = 直连网关 HTTP)
```

| 组件 | 职责 | 复用/新增 |
|---|---|---|
| **ReplayLoader** | 从 Storage 加载 event+chain+attribution+measurements → 组装喂 LLM 的 Replay 上下文 JSON | 新增 |
| **PatternExtractor** | LLM 读 Replay 上下文 → `_PatternLLM` → Pattern（post_process 调 Embedder 嵌入 event_summary） | 新增（继承 `LLMNode`，用 `Reasoner`） |
| **Embedder** | text → float32 向量（网关 embedding-3）；text-hash 缓存；失败返回 None | 新增（`llm/embedder.py`，直连 HTTP，非 claude CLI） |
| **PatternStore** | save_pattern / get_pattern / search（cosine + 枚举/子串过滤, top-K）；source_replay_id 唯一 | 新增（扩 Storage 加 `patterns` 表） |
| **FindSimilarService** | query(desc, filters) → Embedder → PatternStore.search → top-K + 签名 + cosine 分数 | 新增 |

### 4.1 PatternExtractor 的 LLM 输出 schema

```python
class _ArchetypalStockOut(BaseModel):
    industry_node: str
    stocks: list[str]
    observed_car_0_20: float | None
    observed_car_0_60: float | None
    persistence_note: str | None

class _PatternLLM(BaseModel):
    shock_dimension: str            # 供给/需求/政策/流动性/情绪
    impact_direction: str           # 利好/利空/中性/混合
    event_type: str
    industries: list[str]
    event_summary: str              # ≤200 字标准化摘要（embedding 输入）
    transmission_template: str      # 事件→产业→标的 的传导逻辑
    archetypal_beneficiaries: list[_ArchetypalStockOut]
    archetypal_victims: list[_ArchetypalStockOut]
```

post_process： 枚举字段转 Enum（非法值→降级默认）； 调 `Embedder.embed(event_summary)` 设 embedding； 构造 `Pattern`。

---

## 5. 数据流

**提炼**（`extract-pattern --replay-id <id>`）：
```
Storage → ReplayLoader(replay_id) → Replay 上下文
  → PatternExtractor(LLM) → _PatternLLM → Pattern
  → Embedder(event_summary) → Pattern.embedding
  → PatternStore.save（source_replay_id 唯一，重跑覆盖）
```

**检索**（`find-similar --desc "..."`）：
```
query(desc, filters) → Embedder(query_desc) → query_vec
  → PatternStore.search(query_vec, filters, top_k)
    = 先按枚举(shock_dimension/impact_direction)+子串(industries)过滤
    → 对候选 embedding 算 cosine → 排序 → top-K
  → 返回 [{pattern, cosine_score, matched_signature}]
```

> embedding 为 null 的 Pattern：向量子集排除，仅在无 embedding 过滤条件时按结构化命中返回。

---

## 6. Embedding 网关接口（spike 实跑确认）

- 端点：`POST https://open.bigmodel.cn/api/paas/v4/embeddings`
- 体：`{"model": "embedding-3", "input": "<text>"}`
- 认证：`Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>`（与 Reasoner 同 key）
- 输出：`data[0].embedding`，dim 2048，中英文均强
- 存储：float32 BLOB（`np.asarray(vec, dtype=np.float32).tobytes()`；反序列化 `np.frombuffer`）
- 缓存：按 `sha1(text)` 缓存向量（避免重复计费）；存 `embedding_model` 字段
- 失败：网关不可达/超时 → 返回 None（Pattern 存 null embedding，向量子集排除，结构化仍可检索）

---

## 7. CLI

```bash
# 从已存 Replay 提炼 Pattern（需 Phase 1 replay 已跑完）
eventreplay extract-pattern --replay-id <id>

# 检索相似历史 Pattern
eventreplay find-similar --desc "某新事件描述" \
    [--shock-dimension 政策] [--direction 利空] [--industry 教育] [--top-k 3]
```

---

## 8. 测试策略

| 层 | 方法 |
|---|---|
| ReplayLoader | mock Storage，断言组装的上下文含 event/chain/attribution/measurements |
| PatternExtractor | mock Reasoner 返回 `_PatternLLM`，断言 Pattern 形状 + 枚举转换 + 调 Embedder；真实 LLM 冒烟（1 个 Replay） |
| Embedder | 真实网关冒烟（dim 2048）+ mock 离线 + text-hash 缓存命中 + 失败→None |
| PatternStore | save + 幂等（同 source_replay_id 覆盖）；合成 embedding 验证 cosine 检索（最近排前）+ 枚举/子串过滤 + null embedding 处理 |
| FindSimilar | mock Embedder + 真实 Store，端到端检索返回 top-K + 签名 + score |

> Phase 2 真实冒烟需先有 Phase 1 Replay（5 种子事件至少跑 1 个并 extract-pattern）。

---

## 9. 非功能与风险

| 项 | 处理 |
|---|---|
| LLM 非确定性（Pattern 漂移） | 低 temperature；记录 model_alias/prompt_hash；source_replay_id 幂等 |
| 网关 embedding 依赖 | text-hash 缓存；失败→null；检索降级为纯结构化 |
| 样本少（5 Pattern） | 检索价值有限；Phase 2 先建机制，价值随 Replay 累积（不阻塞） |
| 暴力 cosine O(N) | 几十 Pattern 无压力；百级以上升 sqlite-vec（平滑路径） |
| embedding BLOB 一致性 | 统一 float32 + dim 2048；embedding_model 记录入库；模型变更需重嵌入 |

---

## 10. 与 Phase 1 / Phase 3 的关系

- **复用 Phase 1**：Reasoner（`--bare --json-schema`）、Storage（加 attribution/patterns 表）、schema（Event/AttributionResult/ImpactMeasurement）、StockMaster（标的代码校验，若 extractor 引用标的）。
- **Phase 1 补丁**：仅持久化 AttributionResult（§2），不改动既有逻辑。
- **Phase 3 就绪**：FindSimilar 输出（top-K Pattern + 签名 + 相似度 + archetypal 标的 + 传导模板）即前瞻映射所需输入；Phase 3 = 新事件 → find-similar → 复用 Phase 1 流水线映射。

---

## 11. 决策日志

- **LLM 辅助提炼**（非规则/非聚类）：用户裁定；符合 LLM-Agent 架构，能归纳抽象传导逻辑。
- **网关 embedding-3**（非本地）：spike 实跑可用（dim 2048），复用现有网关凭证。
- **暴力 cosine + BLOB**（非 sqlite-vec）：YAGNI；样本少时 O(N) 足够；留升级路径。
- **观察单值非区间**（自审修正）：N=1 给不出区间，不臆造；区间待多 Replay 聚合。
- **枚举过滤非 event_type**（自审修正）：event_type 自由词表不可靠，过滤用 shock_dimension/impact_direction 枚举。

---

## 实现期状态回写（2026-10-19）

> 本节为 Phase 2 实现完成后的回写；上文为 2026-07-01 的设计历史，未改写。

- **落地情况**：Pattern schema（`src/eventreplay/schema/pattern.py`，含 §3 全部字段）已落地；`extract-pattern` / `find-similar` 可用；本地库 `data/eventreplay.db` 已有 **58 个 Pattern** 入库（2016–2025 年事件，`created_at` 均为 2026-08-26，全部含五维定性与 embedding）。年份分布：2016:10 / 2017:7 / 2018:3 / 2019:4 / 2020:4 / 2021:6 / 2022:7 / 2023:8 / 2024:4 / 2025:5。61 个 events 中 3 个暂无 Pattern（`china_japan_dualuse_2026`、`hk_tencent_dump`、`india_pakistan_2025`）。
- **Embedder 实际口径（偏离 §6 固定网关设想）**：`llm/embedder.py` 的 `EMBEDDING_BACKEND` 默认 `auto`——有 `ZHIPU_API_KEY` 且网关可达时走智谱 `embedding-3`（2048 维），否则回退本地 `bge-small-zh-v1.5`（512 维）；首次选定后锁定（`cache/embedder_backend.txt`）不切换，以保证同库向量维度一致。§6 的“固定网关 embedding-3”仅为在线分支口径。
- **补记：Pattern 五维定性**：实现期新增 `PatternQualitative`（**程度 / 范围 / 持续 / 显著 / 超预期** 五维档位）随 Pattern 落库；原 §3 设计未包含，属实现期扩展（Phase 3 前瞻定性 + 盘后对账共用 `schema/pattern.py` 的 `QUALITATIVE_LABELS`）。
- **未实现（维持原非目标）**：Pattern 聚类合并、跨 Replay 区间聚合、`sqlite-vec` 向量库均未做；检索仍为 SQLite + BLOB + 暴力 cosine（58 个 Pattern 规模下无压力）。
