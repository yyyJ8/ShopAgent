# Phase 1：MCP Server + LangGraph Agent 骨架

> 目标：搭通首条链路——自然语言提问 → Agent 选工具 → 拿数据 → 解读返回。
> 时间：第 1–2 周（约 4 个周末半天 + 碎片时间）。
> 修订日期：2026-07-22（基于真实数据库结构重写）。

---

## 核心原则

**以原始表为主（postings / returns / finance_transactions / stocks / ad_*），sku_daily_summary 作为辅助参考（派生数据，可能有误差）。工具数量不设限，充分利用数据库里的每一张业务表。**

---

## 0. 数据库全景 & 数据流向

### 原始表（source of truth）→ Agent 直接使用

```
products                 商品主数据（name/price/category/status）
postings                 订单/发货数据（jsonb products 含 SKU 明细）
returns                  退货记录（数量 + 退货原因）
finance_transactions     财务流水（按 operation_type 拆分每笔费用）
stocks                   实时库存快照（present/reserved × FBO/FBS）
ad_campaigns             广告活动主数据（title/type/state/budget）
ad_campaign_sku_map      广告活动 ↔ SKU 映射
ad_sku_daily_stats       广告表现（SKU × 活动 × 天）
ad_daily_stats           广告活动级日统计（无 SKU 维度，冗余度较高）
```

### 派生表 → 辅助参考

```
sku_daily_summary        ETL 聚合的 SKU 日损益表
                         ⚠️ 数据可能不准确，关键分析时需交叉对比原始表
```

### 非业务表 → Agent 不需要

```
sync_log                 数据同步日志（运维用）
```

---

## 1. 项目结构初始化

```
OzonAgent/
├── plan/                    # 规划文档（已有）
├── src/
│   ├── mcp_server/          # MCP Server：语义化数据工具
│   │   ├── __init__.py
│   │   ├── server.py        # MCP Server 入口，注册 7 个 tool
│   │   ├── tools.py         # 工具实现（固定 SQL + 参数校验）
│   │   └── db.py            # PostgreSQL 连接 + 固定 SQL 查询
│   ├── agent/               # LangGraph Agent：智能分析编排
│   │   ├── __init__.py
│   │   ├── graph.py         # LangGraph 状态机定义
│   │   ├── state.py         # AgentState 类型定义
│   │   └── prompts.py       # Prompt 模板集中管理
│   ├── config/              # 业务口径配置文件
│   │   └── metrics.yaml     # 动销率/转化率/异常阈值等
│   └── app.py               # 命令行入口（后续加 Streamlit）
├── scripts/                 # 工具脚本
│   └── explore_db.py        # 数据库结构探索脚本
├── tests/
│   ├── test_mcp_tools.py    # MCP 工具单测
│   └── test_agent.py        # Agent 链路测试
├── data/
│   └── eval_questions.json  # 评测问题（Phase 2 主要用，先建文件）
├── requirements.txt
├── .env                     # DB 连接串等
└── .gitignore
```

---

## 2. MCP 工具设计（7 个）

### 工具总览

```
MCP Server: ozon-data
├── ① get_products              商品主数据
├── ② get_postings              订单/发货（原始销售数据）
├── ③ get_returns               退货数据（含退货原因）
├── ④ get_finance_transactions  财务流水
├── ⑤ get_stock_snapshot         实时库存
├── ⑥ get_ad_performance         广告表现
└── ⑦ get_daily_summary          日汇总（⚠️ 派生数据，辅助参考）
```

---

### 工具 ①：`get_products` — 商品主数据

```
表：products

入参：
  - sku_ids: [int]         可选，不传返回全部
  - status: str             可选，筛选状态（如 "active"）
  - is_archived: bool       可选，是否已归档
  - category_id: int        可选，按类目筛选

SQL 模板：
  SELECT sku_id, product_id, name, offer_id, category_id,
         price, old_price, min_price, commission_fbo_pct,
         volume_weight, status, is_archived,
         primary_image, created_at, updated_at
  FROM ozon.products
  WHERE ($1::bigint[] IS NULL OR sku_id = ANY($1))
    AND ($2::varchar IS NULL OR status = $2)
    AND ($3::boolean IS NULL OR is_archived = $3)
    AND ($4::int IS NULL OR category_id = $4)
  ORDER BY sku_id

适用场景：
  - "一共有多少个 SKU？哪些在售？"
  - "SKU-123 的基本信息是什么？"
  - "类目 5 下面有哪些商品？"
```

---

### 工具 ②：`get_postings` — 订单/发货数据（原始销售）

```
表：postings

入参：
  - date_range: {start, end}      必填，按 created_at 筛选
  - status: str                   可选，如 "delivered" / "cancelled"
  - delivery_schema: str           可选，FBO / FBS
  - cancel_reason_id: int         可选，按取消原因筛选

SQL 模板：
  SELECT posting_number, order_number, delivery_schema,
         status, cancel_reason_id,
         created_at, in_process_at, delivered_at,
         products       -- jsonb，包含该订单下的 SKU、数量、价格
  FROM ozon.postings
  WHERE created_at BETWEEN $1 AND $2
    AND ($3::varchar IS NULL OR status = $3)
    AND ($4::varchar IS NULL OR delivery_schema = $4)
    AND ($5::int IS NULL OR cancel_reason_id = $5)
  ORDER BY created_at DESC

注意事项：
  - products 字段是 jsonb，SQL 不解析，原样返回
  - Agent 可以自己解析 JSON 提取 SKU 信息
  - 如果要按 SKU 筛选，建议结合 get_daily_summary 先找相关订单号

适用场景：
  - "最近 7 天有多少订单？发货了多少？"
  - "有多少订单被取消了？取消原因是什么？"
  - "FBO 和 FBS 的订单占比？"
  - "某个订单的详细状态？"
  - "下单到发货平均要多久？"（created_at vs in_process_at vs delivered_at）
```

---

### 工具 ③：`get_returns` — 退货数据

```
表：returns

入参：
  - date_range: {start, end}      必填，按 returned_at 筛选
  - sku_ids: [bigint]             可选
  - type: str                     可选，退货类型
  - return_reason_name: str       可选，退货原因关键词

SQL 模板：
  SELECT r.id, r.posting_number, r.sku,
         r.type, r.return_reason_name,
         r.quantity, r.price,
         r.visual_status,
         r.status_changed_at, r.returned_at, r.finished_at,
         r.schema AS delivery_schema,
         p.name AS product_name
  FROM ozon.returns r
  LEFT JOIN ozon.products p ON r.sku = p.sku_id
  WHERE r.returned_at BETWEEN $1 AND $2
    AND ($3::bigint[] IS NULL OR r.sku = ANY($3))
    AND ($4::varchar IS NULL OR r.type = $4)
    AND ($5::varchar IS NULL OR r.return_reason_name ILIKE '%' || $5 || '%')
  ORDER BY r.returned_at DESC

适用场景：
  - "最近退货率最高的 5 个 SKU？"
  - "退货最多的原因是什么？"
  - "SKU-123 的退货问题集中在哪里？"
  - "FBO vs FBS 哪个退货率更高？"
```

---

### 工具 ④：`get_finance_transactions` — 财务流水（原始财务）

```
表：finance_transactions

入参：
  - date_range: {start, end}      必填，按 operation_date 筛选
  - sku_id: bigint                可选
  - operation_type: str           可选
  - type: str                     可选（收入/支出）

SQL 模板：
  SELECT operation_id, operation_type, operation_type_name,
         type, operation_date,
         sku_id, item_name, posting_number, delivery_schema,
         amount, accruals_for_sale, sale_commission,
         delivery_charge, return_delivery_charge,
         services, items
  FROM ozon.finance_transactions
  WHERE operation_date BETWEEN $1 AND $2
    AND ($3::bigint IS NULL OR sku_id = $3)
    AND ($4::varchar IS NULL OR operation_type = $4)
    AND ($5::varchar IS NULL OR type = $5)
  ORDER BY operation_date DESC, amount DESC

适用场景：
  - "这个月花了多少佣金？物流费？仓储费？"
  - "退货产生的逆向物流费有多少？"
  - "有没有异常的扣费项目？"
  - "按 operation_type 对费用做分类汇总"
```

---

### 工具 ⑤：`get_stock_snapshot` — 实时库存

```
表：stocks LEFT JOIN products

入参：
  - sku_ids: [bigint]             可选
  - source: str                    可选，FBO / FBS
  - low_stock_threshold: int      可选，只返回 present ≤ 此值的（断货预警）

SQL 模板：
  SELECT s.sku_id, p.name, p.offer_id, p.status AS product_status,
         s.source,
         s.present, s.reserved,
         s.updated_at
  FROM ozon.stocks s
  LEFT JOIN ozon.products p ON s.sku_id = p.sku_id
  WHERE ($1::bigint[] IS NULL OR s.sku_id = ANY($1))
    AND ($2::varchar IS NULL OR s.source = $2)
    AND ($3::int IS NULL OR s.present <= $3)
  ORDER BY s.present ASC

⚠️ stocks 表有两个 sku_id 字段，SQL 中只能引用一个，部署前需确认哪个是正确的。

适用场景：
  - "哪些 SKU 快断货了？"
  - "FBO 仓和 FBS 仓的库存各有多少？"
  - "库存充足的品有哪些（可以加大广告投放）？"
  - "某个 SKU 当前库存状态？"
```

---

### 工具 ⑥：`get_ad_performance` — 广告表现

```
表：ad_sku_daily_stats LEFT JOIN ad_campaigns

入参：
  - date_range: {start, end}      必填，最大 90 天
  - sku_ids: [bigint]             可选
  - campaign_ids: [varchar]       可选
  - campaign_state: str            可选，如 "active"

SQL 模板：
  SELECT a.stat_date, a.campaign_id,
         c.title AS campaign_title, c.campaign_type, c.state AS campaign_state,
         c.budget AS campaign_budget,
         a.sku_id, a.sku_name, a.sku_price,
         a.impressions, a.clicks, a.ctr,
         a.add_to_cart, a.avg_cpc,
         a.spend,
         a.sold_units, a.sales_promotion, a.total_ordered,
         a.drr_promotion, a.drr_total,
         a.date_added
  FROM ozon.ad_sku_daily_stats a
  LEFT JOIN ozon.ad_campaigns c ON a.campaign_id = c.campaign_id
  WHERE a.stat_date BETWEEN $1 AND $2
    AND ($3::bigint[] IS NULL OR a.sku_id = ANY($3))
    AND ($4::varchar[] IS NULL OR a.campaign_id = ANY($4))
    AND ($5::varchar IS NULL OR c.state = $5)
  ORDER BY a.stat_date, a.spend DESC

适用场景：
  - "最近 7 天广告花了多少？投产比怎么样？"
  - "哪个广告活动效果最差（DRR 最高）？"
  - "SKU-123 的广告 CTR 趋势？"
  - "搜索广告 vs 其他类型广告的对比？"
  - "预算花完了吗？（budget vs spend）"
```

---

### 工具 ⑦：`get_daily_summary` — 日汇总（⚠️ 派生数据）

```
表：sku_daily_summary LEFT JOIN products

⚠️ 注意事项（会写在 tool description 里让 Agent 看到）：
  - 此表为 ETL 聚合结果，数据来源于 postings / finance_transactions / returns 等原始表
  - 可能在以下场景存在数据误差：同步延迟、ETL 逻辑变更、data_quality 字段标记不为 "good"
  - 建议：概览和趋势分析使用此表；关键数据决策时交叉对比原始表（②③④）

入参：
  - date_range: {start, end}      必填，最大 90 天
  - sku_ids: [bigint]             可选
  - data_quality: str             可选，默认不过滤或只取 "good"

SQL 模板：
  SELECT d.date, d.sku_id, p.name, p.offer_id, p.status AS product_status,
         d.ordered_units, d.delivered_units, d.returns_units, d.cancelled_units,
         d.revenue, d.returns_amount,
         d.commissions, d.logistics_costs, d.storage_fees,
         d.advertising, d.promotion_costs, d.other_costs,
         d.net_profit, d.profit_margin,
         d.stock_present, d.stock_reserved,
         d.data_quality
  FROM ozon.sku_daily_summary d
  LEFT JOIN ozon.products p ON d.sku_id = p.sku_id
  WHERE d.date BETWEEN $1 AND $2
    AND ($3::bigint[] IS NULL OR d.sku_id = ANY($3))
    AND ($4::varchar IS NULL OR d.data_quality = $4)
  ORDER BY d.date, d.net_profit DESC

适用场景：
  - 快速概览："最近 7 天整体经营情况？"
  - 趋势分析："销量和利润率的周度变化"
  - 交叉验证："daily_summary 说利润降了，调 finance_transactions 验证具体费用"
```

---

## 3. 工具实现要点

- 每个工具内部是**固定 SQL**，参数只做占位符替换（`WHERE date BETWEEN $1 AND $2`），不拼接字符串
- 参数校验在工具入口做：日期格式校验、范围合法性（不超过 90 天）、sku_id 存在性校验
- 返回结构化 JSON，附带 `row_count` 元信息，便于 Agent 判断数据量
- 工具描述写清楚输入输出格式 + 数据可靠度——这是 Agent 选工具的判断依据
- **SQL 只做 SELECT + WHERE + ORDER BY，不做聚合、不定义业务口径。口径交给 Agent（LLM）+ `metrics.yaml` 配置文件**

---

## 4. 7 工具对比：数据来源 & 可靠度

| 工具 | 数据来源 | 可靠度 | 适用场景 |
|---|---|---|---|
| ① get_products | 原始表 | ⭐⭐⭐ 高 | 商品查询 |
| ② get_postings | 原始表 | ⭐⭐⭐ 高 | 订单/发货跟踪 |
| ③ get_returns | 原始表 | ⭐⭐⭐ 高 | 退货分析 |
| ④ get_finance_transactions | 原始表 | ⭐⭐⭐ 高 | 财务审计/明细 |
| ⑤ get_stock_snapshot | 原始表 | ⭐⭐⭐ 高 | 库存查询 |
| ⑥ get_ad_performance | ad_* 表 | ⭐⭐ 中（来自 OZON API 同步） | 广告分析 |
| ⑦ get_daily_summary | ETL 派生 | ⭐ 低-中（可能有误差） | 概览/趋势，交叉验证 |

---

## 5. LangGraph Agent（智能分析编排层）

### 5.1 AgentState 设计

```python
class AgentState(TypedDict):
    # 用户输入
    user_query: str              # 原始自然语言问题

    # 意图 & 工具选择
    intent: str                  # "lookup" | "anomaly" | "advice" | "chat"
    selected_tools: list[str]    # 需要调用的工具名列表
    tool_params: dict            # 每个工具的调用参数

    # 工具调用结果
    tool_results: dict           # {tool_name: [rows]}

    # 分析输出
    analysis: str                # 数据解读（交叉分析）
    anomalies: list[dict]        # 异常列表 [{type, sku, detail, severity}]
    suggestions: list[str]       # 运营建议

    # 最终回复
    final_answer: str            # 给用户的最终回答
```

### 5.2 状态机流程

```
START
  │
  ▼
[理解问题] ──→ 判断意图 (lookup / anomaly / advice / chat)
  │               识别涉及的工具 + 提取参数（日期/SKU）
  ▼
[选择工具] ──→ 确定需要调哪几个工具、传什么参数
  │              （可能同时调 postings + returns + ad 做交叉分析）
  ▼
[调用工具] ──→ 逐个调 MCP tool，收集结果并入 state
  │              单个工具失败不崩，标记 error 继续
  ▼
[交叉分析] ──→ 把多个工具的结果拼在一起解读
  │              LLM 分析：趋势、对比、关联
  │              对 ⑦ 的派生数据，主动提醒可交叉验证原始表
  ▼
[异常检测] ──→ 规则扫描（阈值/环比/同比）打标记
  │              LLM 对标记做归因判断
  ▼
[运营建议] ──→ 基于异常 + 数据特征生成可执行建议
  │
  ▼
[生成回复] ──→ 组装最终答案，带数据支撑
  │
  ▼
 END
```

### 5.3 节点职责

| 节点 | 谁做 | 输入 | 输出 |
|---|---|---|---|
| **understand** | LLM | user_query | intent + 候选工具 + 参数 |
| **route** | 代码 | intent + 工具列表 | 确定调用顺序（并行/串行） |
| **call_tools** | MCP | 工具名 + 参数 | 结构化数据 |
| **analyze** | LLM | 多工具数据 | 分析文本 |
| **detect_anomalies** | 规则 + LLM | 数据 | 异常列表 |
| **suggest** | LLM | 异常 + 分析 | 建议列表 |
| **respond** | LLM | 全部上下文 | 最终回答 |

### 5.4 路由策略（简单/复杂问题分流）

| 问题类型 | 调用工具数 | 走哪些节点 | 模型 |
|---|---|---|---|
| 简单查数 | 1 个 | understand → route → call_tools → respond | 小模型 |
| 交叉分析 | 2+ 个 | 全链路 | 强模型 |
| 闲聊 | 0 个 | understand → respond（直接答） | 任意 |

### 5.5 状态机实现骨架

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("understand", understand_node)
    builder.add_node("route", route_node)
    builder.add_node("call_tools", call_tools_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("detect_anomalies", detect_anomalies_node)
    builder.add_node("suggest", suggest_node)
    builder.add_node("respond", respond_node)

    builder.set_entry_point("understand")
    builder.add_conditional_edges("understand", route_decision)
    builder.add_edge("route", "call_tools")
    builder.add_edge("call_tools", "analyze")
    builder.add_edge("analyze", "detect_anomalies")
    builder.add_edge("detect_anomalies", "suggest")
    builder.add_edge("suggest", "respond")
    builder.add_edge("respond", END)

    return builder.compile(checkpointer=MemorySaver())
```

---

## 6. Agent 能做的事（基于 7 个工具）

### 查数层

```
✅ 商品信息查询                  → ①
✅ 订单量/发货量/取消率           → ②
✅ 退货率/退货原因分布             → ③
✅ 费用明细（佣金/物流/仓储等）     → ④
✅ 实时库存/FBO-FBS分布           → ⑤
✅ 广告投放表现/投产比             → ⑥
✅ 快速概览（销量/利润/成本一览）   → ⑦（+ ②③④交叉验证）
```

### 交叉分析层

```
✅ "卖得好的品退货率高不高？"                → ② + ③
✅ "广告烧最多的品，实际订单跟上了吗？"        → ⑥ + ②
✅ "利润下降是因为佣金涨了还是退货多了？"       → ④ + ③
✅ "库存预警的品，广告是不是没跟上？"           → ⑤ + ⑥
✅ "daily_summary 数据显示 X，原始表验证 Y"   → ⑦ + ②/③/④
```

### 异常检测层

```
✅ 订单异常：取消率飙升、发货延迟              → ②
✅ 退货异常：某 SKU 退货率突增、某原因集中      → ③
✅ 费用异常：单笔大额扣费、某类费用环比暴增     → ④
✅ 库存预警：断货风险、滞销积压                → ⑤
✅ 广告异常：DRR 过高、CTR 骤降               → ⑥
✅ 利润恶化：profit_margin 持续下降            → ⑦ → ④交叉验证找根因
```

---

## 7. 配置文件设计（业务口径）

### 7.1 `config/metrics.yaml` 结构

```yaml
metrics:
  动销率:
    formula: "有订单SKU数 / 总SKU数 × 100%"
    warning_threshold: 30   # 低于此值预警
    description: "反映库存周转健康度"

  广告DRR:
    formula: "广告花费 / 广告带来销售额 × 100%"
    warning_threshold: 30   # 高于此值预警
    description: "广告投入产出比"

  转化率:
    formula: "订单数 / 点击数 × 100%"
    warning_threshold: 1.5  # 低于此值预警

anomaly_rules:
  订单取消率:
    type: "环比"
    threshold: 0.3          # 较前7天均值升30%触发
    window_days: 7

  退货率飙升:
    type: "环比"
    threshold: 0.5          # 较前7天均值升50%触发
    window_days: 7

  DRR异常:
    type: "阈值"
    threshold: 30           # DRR > 30% 触发

  库存预警:
    type: "阈值"
    days_of_cover_low: 7    # 可售天数 < 7 触发
    days_of_cover_high: 90  # 可售天数 > 90 触发（滞销）
```

### 7.2 注入方式

- 启动时从 YAML 加载到 `AgentState` 的 `config` 字段
- Prompt 模板中通过变量注入关键口径和阈值
- Agent 分析时引用配置中的定义，保证回答一致性

---

## 8. Prompt 管理策略

不把 prompt 散落在代码里，集中到 `prompts.py`：

```python
# 意图识别
UNDERSTAND_PROMPT = """你是 OZON 电商数据分析助手。
用户的自然语言问题，你需要判断：
1. intent: lookup(查数据) / anomaly(异常检测) / advice(运营建议) / chat(闲聊)
2. tools: 需要调用哪些工具
3. params: 从问题中提取的日期范围、SKU 等参数

可用工具：
{tool_descriptions}

用户问题：{query}
"""

# 交叉分析
ANALYZE_PROMPT = """基于以下数据进行分析：
业务口径：
{config_metrics}

各工具返回数据：
{all_tool_results}

⚠️ 注意：get_daily_summary 返回的是 ETL 派生数据，可能存在误差。
如果关键结论依赖此数据，建议提醒用户交叉对比原始表（postings / finance_transactions / returns）。

请分析：趋势变化、数据关联、值得关注的点。
"""
# ... 其余 prompt 模板
```

---

## 9. 异常检测策略（规则 + LLM 混合）

| 环节 | 谁做 | 为什么 |
|---|---|---|
| **检测**（是否异常） | 规则（阈值 / 同比 / 环比） | 确定性、可复现、零幻觉 |
| **归因**（为什么异常） | LLM 推理 | 发挥模型推理能力，多维度交叉分析 |
| **建议**（怎么办） | LLM 生成 | 需要业务知识 + 自然语言表达 |

---

## 10. 开发顺序（按天拆分）

### 第 1 个周末：数据层搭通
- [ ] `src/mcp_server/db.py` — PostgreSQL 连接池 + 7+ 条固定 SQL
- [ ] `src/mcp_server/tools.py` — 7 个工具函数实现（参数校验 + 调 db + 格式化返回）
- [ ] `src/mcp_server/server.py` — MCP Server 入口，注册工具
- [ ] 单测：每个工具调通、参数校验生效、SQL 结果正确

### 第 2 个周末：Agent 骨架
- [ ] `src/agent/state.py` — AgentState 类型定义
- [ ] `src/agent/prompts.py` — 节点 prompt 模板 + 工具描述
- [ ] `src/agent/graph.py` — LangGraph 状态机（7 节点 + 条件路由）
- [ ] 端到端跑通：输入问题 → 返回解读（先不走异常和建议，跑通查数链）

### 第 3 个周末：异常 + 建议 + 配置
- [ ] `src/config/metrics.yaml` — 业务口径 + 异常规则（基于真实字段）
- [ ] 异常检测节点：规则扫描 + LLM 归因
- [ ] 运营建议节点：基于异常生成建议
- [ ] 路由策略：简单问题短路，复杂问题走全链路
- [ ] 全链路跑通：查数 → 分析 → 异常 → 建议

### 第 4 个周末：配置文件 + 打磨
- [ ] 配置文件加载 + prompt 注入验证
- [ ] 多轮对话（checkpointer 验证）
- [ ] 错误处理 + 边界情况
- [ ] 命令行入口 `src/app.py`（先命令行交互，Streamlit 后续加）

---

## 11. 首条链路验收标准

输入：
> "最近 7 天订单量怎么样？退货多吗？哪个品退货率最高？广告投产比如何？"

Agent 行为：
1. 识别意图 `lookup`，提取时间 `last_7_days`
2. 并行调 `get_postings` + `get_returns` + `get_ad_performance`，`get_daily_summary` 作为辅助概览
3. 交叉分析：
   - 订单量趋势（② get_postings）
   - 退货率排名 + 退货原因分布（③ get_returns）
   - 广告 DRR × 销量对比（⑥ get_ad_performance）
4. 对于从 ⑦ 得到的概览数据，主动提醒用户可交叉验证原始表
5. 输出解读，带具体数据和排名

Phase 1 完成标志：这条链路稳定跑通，输出准确、可复现。

---

## 12. 依赖安装

```bash
source venv/Scripts/activate
pip install mcp langgraph langgraph-checkpoint asyncpg pyyaml python-dotenv openai
pip freeze > requirements.txt
```

---

## 关键决策记录

| 决策 | 选什么 | 为什么不选另一个 |
|---|---|---|
| **不做 Text-to-SQL** | 语义化工具 + 固定 SQL | 准

确率 100%、安全边界清晰 |
| **原始表优先，派生表辅助** | 看板数据只用 sku_daily_summary 做概览 | ETL 可能有误差，关键结论需交叉验证 |
| **工具数量不设限** | 7 个，按数据源拆分 | 充分利用数据库，不强行合并 |
| **SQL 只透传，不聚合** | SELECT + WHERE，不定义口径 | 口径交给 Agent + 配置文件 |
| **不用 RAG** | 配置文件注入业务口径 | 仅二十来条口径，RAG 过度工程 |
| **Agent 不碰 SQL** | MCP 工具封装 | 安全边界清晰，Agent 只管"调哪个工具+传什么参" |
| **规则检测 + LLM 归因** | 混合策略 | 检测要可靠（规则），归因要智能（LLM），各取所长 |
