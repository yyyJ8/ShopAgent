# Phase 1：MCP Server + LangGraph Agent

> 状态：已完成。自然语言提问 → Agent 选工具 → 拿数据 → 数据分析 → 异常检测 → 运营建议，完整闭环。
> 修订日期：2026-07-27

---

## 核心原则

以原始表为主（postings / returns / finance_transactions / stocks / ad_*），sku_daily_summary 作为辅助参考（派生数据）。工具不设限，充分利用数据库里的每一张业务表。

**Agent 不碰 SQL** —— MCP 工具封装所有数据访问，Agent 只管"调哪个工具 + 传什么参"。

**规则检测 + LLM 归因** —— 检测靠代码（确定性、零幻觉），归因靠 LLM（多维度推理）。

---

## 数据库全景

### 原始表 9 张

| 表 | 用途 |
|----|------|
| products | 商品主数据（name/price/category/status） |
| postings | 订单/发货数据（products 字段为 jsonb，含 SKU 明细） |
| returns | 退货记录（数量 + 退货原因，SKU 列名为 `sku`） |
| finance_transactions | 财务流水（按 operation_type 拆分每笔费用） |
| stocks | 实时库存快照（present/reserved x FBO/FBS） |
| ad_campaigns | 广告活动主数据（title/type/state/budget） |
| ad_campaign_sku_map | 广告活动 - SKU 映射 |
| ad_sku_daily_stats | 广告表现（SKU x 活动 x 天），含 ctr/drr_total/spend/sold_units |
| ad_daily_stats | 广告活动级日统计（impressions/clicks/spend/orders_count/orders_sum） |

### 派生表 1 张

| 表 | 用途 |
|----|------|
| sku_daily_summary | ETL 聚合的 SKU 日损益表，关键结论需交叉对比原始表 |

---

## MCP 工具（8 个）

```
MCP Server: ozon-data
+-- 1 get_products              商品主数据
+-- 2 get_postings              订单/发货
+-- 3 get_returns               退货数据
+-- 4 get_finance_transactions  财务流水
+-- 5 get_stock_snapshot        实时库存
+-- 6 get_ad_performance        广告 SKU 粒度日表现
+-- 7 get_ad_campaign_stats     广告计划粒度日统计
+-- 8 get_daily_summary         日汇总（派生数据，辅助参考）
```

所有工具支持可选 `store_id`（不传 = 全平台汇总），日期范围最大 90 天，参数化防注入。

### 可靠度分级

| 可靠度 | 工具 |
|--------|------|
| 高 | get_products / get_postings / get_returns / get_finance_transactions / get_stock_snapshot（原始表） |
| 中 | get_ad_performance / get_ad_campaign_stats（OZON API 同步） |
| 低-中 | get_daily_summary（ETL 派生，可能有误差） |

---

## LangGraph Agent（8 节点 + 5 条件路由 + 数据完整性环）

### 架构

```
START -> 1 understand -> 2 plan -> 3 call_tools -> 4 data_check -> 5 analyze -> 6 detect -> 7 suggest -> 8 respond -> END
                              ^                     |
                              +--- 补调 (<=1 次) ---+
                                      不完整时回 plan，>=2 轮熔断降级
```

### 节点职责

| # | 节点 | 类型 | 模型 | 说明 |
|----|------|------|------|------|
| 1 | understand | LLM | simple | 意图分类（chat/lookup/anomaly/advice）+ 实体提取 |
| 2 | plan | LLM + FC | full + tools | Function Calling 选工具 + 填参数；补调轮追加缺失数据源提示 |
| 3 | call_tools | 代码 | -- | MCP streamable-http 并行执行，补调时去重已调工具 |
| 4 | data_check | 代码 | -- | 检查 anomaly_rules 所需数据源覆盖，缺失 -> plan，>=2 轮熔断 |
| 5 | analyze | LLM | full | 数据解读 + 交叉验证 + 派生数据可靠性警告 |
| 6 | detect | 代码 + LLM | full | 6 条规则扫描（多源合并 + 聚合 + 比例重算）+ LLM 归因 |
| 7 | suggest | LLM | full | 可执行运营建议（具体到 SKU/类目/店铺） |
| 8 | respond | LLM | simple | 组装最终回答，面向运营人员 |

### 路由策略

```
chat 意图       -> understand -> respond（2 节点）
lookup 意图     -> 跳过 data_check/detect/suggest
anomaly/advice  -> 全链路 + 数据完整性环
plan 无 tool_calls -> respond
```

### LLM 配置

两个实例，同一模型（DeepSeek V4 Pro，langchain-openai ChatOpenAI 接入）：
- `simple_llm`：temperature=0.1，用于分类和格式化
- `full_llm`：temperature=0.3，用于分析/规划/归因
- `plan_llm`：full_llm.bind_tools(tool_defs)，动态注入 MCP 工具列表

---

## 异常检测：6 条规则

全部在 `src/config/metrics.yaml` 中配置，改 yaml 不动代码。

| 规则 | 条件 | 数据源 |
|------|------|--------|
| SKU退货异常 | returns_units >= 4 AND ordered_units >= 20 | get_daily_summary |
| SKU利润率预警 | profit_margin < 10 | get_daily_summary |
| 广告DRR过高 | drr_total >= 5 AND spend >= 500 | get_ad_performance |
| 高点击低转化 | ctr >= 3 AND spend >= 300 AND sold_units <= 5 | get_ad_performance |
| SKU断货风险 | present <= 10 | get_stock_snapshot |
| SKU滞销积压 | ordered_units == 0 AND stock_present >= 20 | get_daily_summary |

### detect 节点实现要点

- **多数据源合并**：按 sku_id 自然 join，兼容 `returns.sku` 列名
- **日粒度聚合**：数值字段 sum（ordered_units/spend/sold_units），比例字段（profit_margin/drr_total/ctr）用 sum(分子)/sum(分母) 重算，快照字段（present）取末值
- **severity_map**：正则从描述文本解析阈值分级（critical/warning）
- **诊断日志**：字段缺失 N/M rows、数据源未找到告警、熔断提示

---

## AgentState（14 字段）

```python
class AgentState(TypedDict, total=False):
    messages: list              # LangGraph 消息历史
    user_query: str             # 本轮原始问题
    intent: str                 # chat | lookup | anomaly | advice
    entities: dict              # {date_range, sku_ids, metrics, store_id}
    tool_results: dict          # {tool_name: {data, row_count, error?}}
    analysis: str               # 数据解读
    anomalies: list             # [{type, severity, detail, attribution}]
    suggestions: list           # 运营建议
    config: dict                # metrics.yaml 加载结果
    final_answer: str           # 最终回答
    error: str                  # 全局错误标记
    plan_iteration: int         # plan 轮次（>=2 熔断）
    missing_sources: list[str]  # 缺失的数据源
    skipped_rules: list[str]    # 因数据不全跳过的规则
```

---

## 项目结构

```
+-- src/
|   +-- mcp_server/         # MCP Server (FastMCP)
|   |   +-- db.py           #   PostgreSQL 连接池 + 8 条固定参数化 SQL
|   |   +-- tools.py        #   8 工具函数
|   |   +-- server.py       #   FastMCP 入口（3 种传输模式）
|   +-- agent/              # LangGraph Agent
|   |   +-- state.py        #   AgentState TypedDict (14 字段)
|   |   +-- prompts.py      #   7 个节点 prompt 模板
|   |   +-- graph.py        #   状态机（8 节点 + 5 条件路由 + 数据完整性环)
|   |   +-- logger.py       #   节点级结构化日志
|   |   +-- mcp_client.py   #   MCP streamable-http 客户端
|   |   +-- tool_adapter.py #   MCP schema -> OpenAI function 格式
|   |   +-- config_loader.py#   metrics.yaml 加载
|   |   +-- run.py          #   CLI 交互入口
|   +-- config/             # 业务口径配置
|       +-- metrics.yaml    #   6 业务口径 + 6 条异常检测规则
+-- data/
|   +-- eval_questions.json # 评测题库（10 题）
|   +-- eval_results.json   # 评测结果
+-- tests/
|   +-- test_mcp_tools.py   # MCP 工具直连验证
|   +-- test_e2e_agent.py   # Agent 端到端测试
|   +-- test_eval.py        # 评测运行脚本
|   +-- test_aggregation.py # 聚合逻辑单元测试
+-- plan/
|   +-- agent-status.md     # 开发状态跟踪
+-- requirements.txt
+-- .env
+-- README.md
```

---

## 评测结果

10 道测试题覆盖 4 种意图 x 6 个 MCP 工具，纯代码自动打分。

| 指标 | 结果 |
|------|------|
| 总分 | **11.5/12.5 (92%)** |
| 意图分类 | 10/10 (100%) |
| 工具选择 | 8/10 (80%) |
| 异常命中 | 5/6 规则生效 |

```bash
MCP_TRANSPORT=http python -m src.mcp_server.server  # 终端 1
python tests/test_eval.py                             # 终端 2
```

---

## 关键决策记录

| 决策 | 选什么 | 为什么不选另一个 |
|------|--------|------------------|
| 不做 Text-to-SQL | 语义化工具 + 固定 SQL | 准确率 100%、安全边界清晰 |
| 原始表优先，派生表辅助 | 7 个原始表工具 + 1 个 ETL 工具 | ETL 可能有误差，关键结论需交叉验证 |
| Agent 不碰 SQL | MCP 工具封装 | Agent 只管"调哪个工具 + 传什么参" |
| MCP streamable-http | 双进程 HTTP 通信 | Agent 和 MCP Server 独立演进、独立部署 |
| Function Calling | LLM 原生 tool_calls | 比 Text-to-JSON 格式更可靠 |
| 规则检测 + LLM 归因 | 混合策略 | 检测要可靠（规则），归因要智能（LLM） |
| 数据完整性环 | plan <-> call_tools <-> data_check | 代码层保证数据源不遗漏，2 轮熔断降级 |
| LangGraph | StateGraph + 条件路由 + MemorySaver | 多分支天然建模，后续加环只需加边 |
| DeepSeek V4 Pro | langchain-openai ChatOpenAI 接入 | OpenAI 兼容 API，切换成本低 |
