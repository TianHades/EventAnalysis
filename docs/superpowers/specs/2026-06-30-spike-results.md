# §4.0 数据可用性 Spike 结果（2026-06-30 实跑）

> 【2026-10-19 回写】本文件为 2026-06-30 的历史 spike 结论；其中“成分股仅一级可用/完全不可得”已被 tushare 申万三级方案替代，“eastmoney/baostock 不可达”与“claude --bare”等结论仍成立。

环境：Windows / Python 3.10.10 / akshare 1.18.25 / baostock 0.8.90 / claude CLI 2.1.177。网络经用户中途修复（szse/sina/legulegu/pythonhosted 恢复；eastmoney/baostock 仍不可达）。本报告为**实跑 ground truth**，用于回写 Phase 1 spec v2.5、替换此前所有 spike-gated 推测项。

---

## 1. 网络可达性（gate 9）

| 源 | 状态 | 影响 |
|---|---|---|
| sina（hq.sinajs.cn / vip.stock.finance.sina.com.cn） | ✅ 可达（403 需 Referer，akshare 已带） | 个股日线、指数、单标的改名走此 |
| szse.cn | ✅ 可达（部分接口偶发 RemoteDisconnected） | 深市全市场改名、退市、基本信息 |
| legulegu.com | ✅ 可达（403，akshare 带参数） | 申万三级行业清单 |
| pythonhosted.org（pip wheel） | ⚠️ 可达但 wheel 下载不稳定（scipy wheel 失败） | 部分包安装受限 |
| **eastmoney（push2his/push2/82.push2）** | ❌ **不可达**（curl 000；akshare ConnectionError RemoteDisconnected） | **阻断 `stock_zh_a_hist`/`index_zh_a_hist`/`stock_zh_a_spot_em`** |
| **baostock（TCP 10087）** | ❌ **不可达**（login 10002007 / WinError 10057） | **baostock 不能作 fallback** |

**结论**：数据层主源必须走 **sina**（非 eastmoney）；baostock 在本环境不可用，不能作为后复权 fallback。

## 2. akshare 接口（§2 基座）

| 用途 | 接口（实测） | 结果 |
|---|---|---|
| **个股后复权日线** | `stock_zh_a_daily(symbol="sh600519", adjust="hfq", start_date, end_date)` | ✅ 返回 [date/open/high/low/close/volume/amount/**outstanding_share**/turnover]。**用此，非 eastmoney 的 stock_zh_a_hist** |
| **指数日线（价格）** | `stock_zh_index_daily(symbol="sh000300")` | ✅ 5938 行 [date/open/high/low/close/volume] |
| **沪深300全收益 H00300.CSI** | `stock_zh_index_daily("H00300.CSI")` | ❌ KeyError 'date'（sina 不提供）；eastmoney 不可达 → **全收益不可得** |
| 交易日历 | `tool_trade_date_hist_sina()` | ✅ 8797 行 [trade_date] |
| 改名（深市全市场） | `stock_info_sz_change_name(symbol="简称变更")` | ✅ 7432 行 [变更日期/证券代码/证券简称/变更前简称/变更后简称]。**入参 `'简称变更'` 实跑正确**（旧值 `'全部股票'` 抛 KeyError 已确认） |
| 改名（单标的） | `stock_info_change_name(symbol)` | ✅ 真代码(000001/600519)→rows≥0 [index,name]；**假/无效代码(999999/000000)→`KeyError: 0`**（非空 DF，实跑确认） |
| 退市（沪） | `stock_info_sh_delist()` | ✅ 156 行 [公司代码/公司简称/上市日期/暂停上市日期] |
| 退市（深） | `stock_info_sz_delist(symbol="终止上市公司")` | ✅ 204 行 [证券代码/证券简称/上市日期/终止上市日期] |
| 申万一级行业清单 | `sw_index_first_info()` | ✅ 31 行（行业代码带 .SI 后缀，如 801010.SI） |
| 申万二级行业清单 | `sw_index_second_info()` | ✅ 131 行（含"上级行业"列） |
| 申万三级行业清单 | `sw_index_third_info()` | ✅ 336 行（如 850111.SI 种子） |
| **申万成分股** | `index_component_sw(symbol)` | ⚠️ **仅一级 `801001` 可用（55 行）**；二级(801010)/三级(850111/850111.SI) 均 `KeyError`（返回结构不含预期列） → **CandidateGen 须用一级成分股（粗于 spec 假设的三级）** |
| 基本信息代码名 | `stock_info_a_code_name()` | ⚠️ flaky（szse RemoteDisconnected 偶发）→ 需替代（交易所 name_code 接口或从行情推导） |
| 市值快照 | `stock_zh_a_spot_em()` | ❌ eastmoney 不可达 → **市值从 `stock_zh_a_daily` 的 outstanding_share × close 推导，无需单独接口** |
| 函数签名可见性 | `inspect.signature(ak.<fn>)` | ⚠️ 一律返回 `()`（akshare 函数被装饰/包装）→ 签名须查文档/源码，不能用 inspect |

## 3. CLI（claude 2.1.177，本地 + GLM 网关）

| 项 | 实测 |
|---|---|
| `--bare` / `--disable-slash-commands` / `--json-schema` / `--model` / `--permission-mode` | ✅ 全部存在（`--help` 原文确认）；无 `--temperature` |
| **`claude -p --bare --disable-slash-commands --model sonnet --permission-mode dontAsk "..."`** | ✅ **正常返回（"OK"，exit 0，<40s）** |
| **不带 `--bare` 的 `claude -p`** | ❌ **挂起 ≥4 分钟无输出**——加载 `.mcp.json` 的 forge_extension MCP 导致。**故 `--bare` 对子进程调用是必须的**（不仅是"干净"，而是避免卡死） |
| `--bare` 鉴权 | ✅ GLM 网关（`ANTHROPIC_AUTH_TOKEN`+`ANTHROPIC_BASE_URL`，3P provider 凭证）可用；`--bare` 在本环境正常鉴权（直连 Anthropic 才严格 ANTHROPIC_API_KEY） |
| `--model` 形式 | alias（`sonnet`）✅ 可用；full-name / dated-id 未在 bare 下干净测出（非 bare 挂起）→ 仍按"记录最具体可用形式"，alias 已确认可用 |

## 4. GRANK 库

| 项 | 实测 |
|---|---|
| `event-study-toolkit`（PyPI） | ⚠️ 可装（--no-deps），但 `dir()` **不直接暴露 GRANK**——PyPI 描述文字称支持，实际模块未提供可用接口。**结论：GRANK 须手写**（调整因子 `Z_adj=Z_BMP/√(1+(L−1)·r̄)` + 蒙特卡洛 size 检验，参考 Kolari-Pynnönen 原文 Table 2） |

## 5. 对 spec 的影响（v2.5 回写要点）

1. **QuoteAdapter 主源换为 sina**：个股后复权 `stock_zh_a_daily`（非 `stock_zh_a_hist`）；指数 `stock_zh_index_daily`
2. **沪深300全收益不可得 → 价格指数 `sh000300` 作主基准 + 报告披露"β 口径含分红偏差"**（不再是"fallback"，是实际路径）
3. **~~申万成分股仅一级可用~~ → 修正：行业成分股完全不可得**（见下方"M0.5 细化"）→ CandidateGen 降级为"LLM 提议 + resolve 校验"
4. **baostock 移出 fallback**（本环境不可达）→ 数据层仅 akshare/sina；网络是已知外部依赖
5. **市值从 `outstanding_share × close` 推导**（无需 spot 接口）
6. **`--bare` 必须用于子进程调用**（避免 forge_extension MCP 挂起），非可选
7. **GRARK 手写**（event-study-toolkit 不提供可用 GRANK）
8. **`stock_info_a_code_name` flaky** → StockMaster 基本信息用 `stock_info_sh_name_code`+`stock_info_sz_name_code`（可达）
9. akshare 函数签名查文档（inspect 不可用）

## 6. M0.5 细化（StockMaster 实现期补充 spike）

实现 StockMaster 时发现 v2.5 第 3 条"一级成分股可用"**不成立**，详查：

- `index_component_sw("801001")` → 55 行，但成分股是**混合行业**（盐湖股份/南方航空/葛洲坝/中兴…），是某个 aggregate/sample，**非单一行业**。
- 所有真正的一级行业代码（`801010` 农林牧渔、`801030` 基础化工、`801110`…，带/不带 `.SI`）→ **全部 KeyError**。
- 结论：`index_component_sw` 在本环境**拉不到任何行业成分股**；申万 stock→行业 映射无可达源。

可达且可用的"真实标的主数据"源：
- `stock_info_sh_name_code()` → 1701 行 [证券代码/证券简称/证券全称/公司简称/公司全称/上市日期]（**无行业**）
- `stock_info_sz_name_code()` → 2894 行 [板块/A股代码/A股简称/A股上市日期/A股总股本/A股流通股本/**所属行业**]（深市，**证监会行业**非申万）

**StockMaster 设计（实现期裁定）**：
- `resolve(identifier)` = 反幻觉核心：以 sh/sz name_code 为真实标的基准 + sz_change_name 曾用名双源交叉 + delist；可用。
- `constituents(industry)` = 行业成分股：**不可得**（index_component_sw 失败）；降级为"深市所属行业关键词过滤"（部分、证监会行业、仅深市），仅作 hint。
- CandidateGeneration 据此降级：**LLM 提议候选（按名称）→ StockMaster.resolve 逐个校验真实性**（反幻觉护栏仍有效）；行业底池检索仅作可选 hint，不可用时全靠 LLM 提议 + resolve。报告披露"行业覆盖依赖 LLM 召回 + 真实性校验，非全市场行业成分股枚举"。

> 这些是**实跑确认的事实**，spec v2.5 据此替换此前所有"待 spike 验证"的推测项——5 轮审查里反复争论的 `--bare`/`--model`/GRANK 库/`stock_info_change_name` 异常等，至此一锤定音。
