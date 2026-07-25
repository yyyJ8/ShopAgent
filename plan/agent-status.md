# Agent 阶段完成状态

> 更新日期：2026-07-25

---

## 已完成

### 基础设施

| 内容 | 文件 | 状态 |
|------|------|------|
| AgentState 类型定义（14 字段） | `src/agent/state.py` | ✅ |
| 7 节点 prompt 模板 | `src/agent/prompts.py` | ✅ |
| LangGraph 状态机（8 节点 + 5 条件路由 + 数据完整性环） | `src/agent/graph.py` | ✅ |
| MCP streamable-http 客户端 | `src/agent/mcp_client.py` | ✅ |
| MCP → OpenAI 工具格式转换 | `src/agent/tool_adapter.py` | ✅ |
| metrics.yaml 配置加载 | `src/agent/config_loader.py` | ✅ |
| 节点级结构化日志 | `src/agent/logger.py` | ✅ |
| CLI 交互入口 | `src/agent/run.py` | ✅ |
| 业务口径 + 异常规则配置 | `src/config/metrics.yaml` | ✅ |
| MCP Server（8 个工具） | `src/mcp_server/` | ✅ |

### Bug 修复

| 问题 | 修复 |
|------|------|
| plan 调同名工具两次时结果互相覆盖 | `mcp_client.py` 用 `{name}#{idx}` 区分 key |
| 店铺对比不传 store_id | `PLAN_SYSTEM` 加第 6 条规则 |
| .env 中 API Key 带引号导致鉴权失败 | 去引号 + 代码层 `strip('"')` 防御 |
| lambda 返回 coroutine 导致 InvalidUpdateError | 改为 `async def _plan_node` 闭包 |

### detect 规则扫描（本次迭代新增）

| 内容 | 说明 |
|------|------|
| `_check_threshold` 完整实现 | 多数据源按 sku_id 合并、5 种操作符、require: all、severity_map 解析 |
| `_resolve_severity` | 正则从 severity_map 描述解析阈值分级（critical/warning） |
| 诊断日志 | 字段缺失统计（N/M rows）、数据源未找到告警 |
| `sku`/`sku_id` 兼容 | `get_returns` 表列名是 `sku`，合并时自动兼容 |

### 数据完整性环（本次迭代新增）

| 内容 | 文件 | 说明 |
|------|------|------|
| `data_check_node` | `graph.py` | 纯代码，检查 anomaly_rules 所需数据源覆盖 |
| `_get_required_sources` | `graph.py` | 从 anomaly_rules 提取 data_source → 规则名映射 |
| `route_after_data_check` | `graph.py` | 完整→analyze，缺失→plan，≥2轮→熔断降级 |
| plan_node 补调提示 | `graph.py` | 缺失数据源时追加 hint 到 system prompt |
| call_tools 合并 | `graph.py` | 补调时保留前一轮 tool_results |

### metrics.yaml 字段修正（本次迭代新增）

| 规则 | 改动 | 原因 |
|------|------|------|
| SKU退货异常 | `return_rate`→`returns_units`, `order_count`→`ordered_units`，去掉 `get_returns` | 字段不存在；`get_returns` 用 `sku` 无法合并 |
| 高点击低转化 | `cvr`→`sold_units` 代理 | `ad_sku_daily_stats` 无转化率字段 |
| SKU滞销积压 | `sales_days`→`ordered_units`, 双源→单源（`stock_present` 同表） | 字段不存在；无需跨源合并 |

### 新 MCP 工具（本次迭代新增）

| 工具 | 数据源 | 说明 |
|------|--------|------|
| `get_ad_campaign_stats` | `ad_daily_stats` | 计划粒度日统计，含 orders_count/orders_sum |

### 验证通过的三条链路

| 路径 | 测试问题 | 结果 |
|------|----------|------|
| chat → respond | "你好，你能做什么？" | 正确分类，友好回复 |
| lookup → plan → call_tools → analyze → respond | "最近7天订单量" | 选工具正确，322 行数据，深度分析 |
| anomaly → 全链路 + 数据完整性环 | "最近30天有没有什么异常？" | 6 工具全覆盖，detect 扫 6 条规则，SKU断货风险 29 条命中 |
| 店铺对比 | "店铺1和店铺2的利润率对比？" | store_id 分别传，Store 1 vs 2 净利率 / 成本结构 |

---

## 待完成

### 1. detect 规则聚合优化（核心缺口）

**问题**：`sku_daily_summary` 和 `ad_sku_daily_stats` 是日粒度，`_check_threshold` 逐行比对，导致 `ordered_units >= 20`（月累计）被拆到每天 3-5 单永远不命中。广告规则同理——单日 spend 不够 500 但月累计远超。

```
要做的：
- _check_threshold 合并行之前，先按 sku_id group by → 数值字段 sum
- stock 类字段取最后一天的值，比例字段（drr_total/profit_margin）暂 sum（不漏报）
- 改动范围：_check_threshold 内部 ~15 行，不改图结构
```

### 2. Agent 单元测试

**文件**：`tests/test_agent.py`（不存在，需新建）

目前只有 `tests/test_mcp_tools.py`（MCP 工具直连测试），Agent 链路没有测试覆盖。

```
要做的：
- 各节点的纯逻辑测试（不依赖 LLM 和 DB）
- _check_threshold / _resolve_severity / _get_required_sources 单元测试
- prompt 模板的变量完整性测试
- state 字段的类型校验
```

### 3. 多轮对话验证

MemorySaver checkpointer 已配置但未测试连续对话场景。

```
要做的：
- 同一 thread_id 下连续提问，验证上下文保持
- 第二轮问题的 tool 选择是否受第一轮结果影响
```

### 4. Phase 2 评测准备

**文件**：`data/eval_questions.json`（空文件）

评测闭环是 ROADMAP 里 Phase 2 的核心——20 道测试题 + 标准答案 + 准确率指标。

```
要做的：
- 设计 20 道覆盖所有工具和意图类型的测试题
- 定义每道题的"正确答案"标准（调了哪些工具 / 关键数据是否正确）
- 写评测运行脚本
```

### 5. Streamlit UI

ROADMAP 中 Phase 2 的内容，先不碰。

---

## 优先级建议

```
P0: detect 规则聚合（日粒度 → 月累计）  ← 当前瓶颈
P1: Agent 单元测试
P2: 评测准备（Phase 2 核心交付物）
P3: 多轮对话
P4: Streamlit UI
```
