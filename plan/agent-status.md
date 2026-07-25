# Agent 阶段完成状态

> 更新日期：2026-07-26

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

### 3. Phase 2 评测准备

**文件**：`data/eval_questions.json`（空文件）

20 道测试题 + 标准答案 + 准确率指标，是 ROADMAP 里 Phase 2 的核心交付物。

### 4. 多轮对话验证

MemorySaver checkpointer 已配置但未测试连续对话场景。

### 5. Streamlit UI

ROADMAP 中 Phase 2 的内容，先不碰。

---

## 优先级

```
P0: detect 规则聚合（日粒度 → 月累计）← 当前瓶颈
P1: Agent 单元测试
P2: 评测准备
P3: 多轮对话
P4: Streamlit UI
```

---

## 本轮迭代问题 & 解决方案

| 问题 | 方案 |
|------|------|
| `_check_threshold` 是空壳，6 条已确认规则全不生效 | 实现完整扫描逻辑：多数据源按 sku_id 合并、5 种操作符、severity_map 正则解析 |
| metrics.yaml 字段名和 DB 列名不对应（`return_rate`/`cvr`/`sales_days`/`order_count` 不存在）| 逐条对比 DB schema 修正为已有字段做代理，`SKU滞销积压` 利用同表 `stock_present` 去掉跨源合并 |
| plan 节点漏调工具（如 `get_ad_performance`），detect 部分规则空跑 | 新增 `data_check_node`（纯代码）→ 检查 anomaly_rules 所需数据源 → 缺失回 plan 补调 → 2 轮熔断降级 |
| 补调时 tool_results 被覆盖 | `call_tools_node` 合并历史结果 |
| 字段缺失时日志看不出来 | 新增诊断日志：字段缺失 N/M rows、数据源未找到告警、熔断提示 |
| `get_returns` 表 SKU 列名是 `sku` 非 `sku_id`，跨源合并失败 | `_check_threshold` 合并时 `row.get("sku_id") or row.get("sku")` |
| `ad_daily_stats` 表有数据但无 MCP 工具 | 补 `get_ad_campaign_stats`（db.py + tools.py + server.py）|
