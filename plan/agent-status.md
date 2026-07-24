# Agent 阶段完成状态

> 更新日期：2026-07-24

---

## 已完成

### 基础设施

| 内容 | 文件 | 状态 |
|------|------|------|
| AgentState 类型定义 | `src/agent/state.py` | ✅ |
| 7 节点 prompt 模板 | `src/agent/prompts.py` | ✅ |
| LangGraph 状态机（7 节点 + 4 路由） | `src/agent/graph.py` | ✅ |
| MCP streamable-http 客户端 | `src/agent/mcp_client.py` | ✅ |
| MCP → OpenAI 工具格式转换 | `src/agent/tool_adapter.py` | ✅ |
| metrics.yaml 配置加载 | `src/agent/config_loader.py` | ✅ |
| 节点级结构化日志 | `src/agent/logger.py` | ✅ |
| CLI 交互入口 | `src/agent/run.py` | ✅ |
| 业务口径 + 异常规则配置 | `src/config/metrics.yaml` | ✅ |

### Bug 修复

| 问题 | 修复 |
|------|------|
| plan 调同名工具两次时结果互相覆盖 | `mcp_client.py` 用 `{name}#{idx}` 区分 key |
| 店铺对比不传 store_id | `PLAN_SYSTEM` 加第 6 条规则 |
| .env 中 API Key 带引号导致鉴权失败 | 去引号 + 代码层 `strip('"')` 防御 |
| lambda 返回 coroutine 导致 InvalidUpdateError | 改为 `async def _plan_node` 闭包 |

### 验证通过的三条链路

| 路径 | 测试问题 | 结果 |
|------|----------|------|
| chat → respond | "你好，你能做什么？" | 正确分类，友好回复 |
| lookup → plan → call_tools → analyze → respond | "最近7天订单量" | 选工具正确，322 行数据，深度分析 |
| anomaly → 全链路 | "哪个 SKU 退货率最高？有异常？" | SKU 排名 + 店铺对比 + 具体建议 |
| 店铺对比 | "店铺1和店铺2的利润率对比？" | store_id 分别传，Stroe 1 vs 2 净利率 / 成本结构 |

### 配置文件

| 内容 | 状态 |
|------|------|
| 业务口径（6 个指标） | ✅ 已确认 |
| 异常规则（6 条复合规则） | ✅ 阈值已和业务人员核对 |

---

## 待完成

### 1. detect 规则扫描代码（核心缺口）

**文件**：`src/agent/graph.py` 第 173-182 行

`_check_threshold` 和 `_check_mom` 目前是空壳，直接 `return []`。metrics.yaml 里已有 6 条确认规则，代码需要能读取 `conditions` 数组、按 `require: all` 逻辑逐条扫描 tool_results 中的数据。

```
要做的：
- _check_threshold: 遍历 conditions，对 tool_results 中对应数据源的每行做字段比对
- 支持 op: gte / lte / lt / eq
- 支持 require: all（全部条件满足才标记）
- 支持 severity_map（区分 critical / warning）
```

### 2. Agent 单元测试

**文件**：`tests/test_agent.py`（不存在，需新建）

目前只有 `tests/test_mcp_tools.py`（MCP 工具直连测试），Agent 链路没有测试覆盖。

```
要做的：
- 各节点的纯逻辑测试（不依赖 LLM 和 DB）
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
P0: detect 规则扫描 → 让 6 条确认规则真正工作，全链路闭环
P1: Agent 单元测试   → 保证改动不引入回归
P2: 评测准备         → Phase 2 的核心交付物
P3: 多轮对话         → 锦上添花
P4: Streamlit UI     → 后续阶段
```
