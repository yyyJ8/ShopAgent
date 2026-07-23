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
├── plan/                    # 规划文档
│   ├── ROADMAP.md
│   └── phase-1.md           # ← 本文档
├── src/
│   ├── mcp_server/          # MCP Server（✅ 已完成）
│   │   ├── __init__.py
│   │   ├── server.py        # FastMCP 入口，注册 7 个 tool，支持 stdio/sse/streamable-http
│   │   ├── tools.py         # 工具实现（参数校验 + 调 db + 格式化返回）
│   │   └── db.py            # PostgreSQL 连接池 + 7 条固定 SQL（参数化防注入）
│   ├── agent/               # LangGraph Agent（📝 待实现）
│   │   ├── __init__.py
│   │   ├── state.py         # AgentState TypedDict
│   │   ├── prompts.py       # 7 个节点的 prompt 模板
│   │   ├── graph.py         # LangGraph 状态机（7 节点 + 条件路由）
│   │   ├── mcp_client.py    # MCP streamable-http 客户端封装
│   │   ├── tool_adapter.py  # MCP tool schema → OpenAI function 格式
│   │   ├── config_loader.py # metrics.yaml 加载
│   │   └── run.py           # CLI 交互入口
│   ├── config/              # 业务口径配置文件
│   │   └── metrics.yaml     # 业务口径 + 异常规则（阈值占位，后续调优）
│   └── __init__.py
├── scripts/                 # 工具脚本
│   └── explore_db.py        # 数据库结构探索脚本
├── tests/
│   ├── test_mcp_tools.py    # MCP 工具单测
│   └── test_agent.py        # Agent 链路测试
├── data/
│   └── eval_questions.json  # 评测问题（Phase 2 主要用，先建文件）
├── requirements.txt
├── .env                     # DB 连接串 + API Key 等
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

## 5. LangGraph Agent（全框架，一次搭好）

> **设计原则**：7 个节点全部实现，后续只需填充规则阈值和调优 prompt。
> **通信方式**：Agent 通过 MCP streamable-http 客户端连接 MCP Server（独立进程）。
> **工具调用**：LLM Function Calling（原生 tool_calls，不手写 JSON 解析）。
> **LLM**：DeepSeek V4 Pro（OpenAI 兼容 API，通过 langchain-openai 的 ChatOpenAI 接入）。

### 5.0 架构总览（两个进程）

```
┌──────────────────────────────────────────────────────────────┐
│  Agent 进程 (python -m src.agent.run)                         │
│                                                               │
│  LangGraph StateMachine                                       │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐    │
│  │understand│ →  plan  │ →  call  │ →analyze │ →detect  │    │
│  │  (LLM)   │  (LLM+FC)│   _tools │  (LLM)   │ (code    │    │
│  │          │          │  (code)  │          │  +LLM)   │    │
│  └──────────┴──────────┴──────────┴──────────┴──────────┘    │
│       ↓                                        ↓              │
│  ┌──────────┐                              ┌──────────┐      │
│  │ suggest  │ ───────────────────────────→  │ respond  │      │
│  │  (LLM)   │                              │  (LLM)   │      │
│  └──────────┘                              └──────────┘      │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ MCP Client (streamable-http)                          │    │
│  │  - 启动时 list_tools() → 拿到 7 个 tool schema         │    │
│  │  - 执行时 call_tool(name, arguments) → 拿到数据        │    │
│  └──────────────────┬───────────────────────────────────┘    │
└─────────────────────┼────────────────────────────────────────┘
                      │ HTTP (streamable-http)
                      │ http://127.0.0.1:8000/mcp
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  MCP Server 进程 (python -m src.mcp_server.server)            │
│  MCP_TRANSPORT=streamable-http                                │
│                                                               │
│  7 个 tool: get_products / get_postings / get_returns /      │
│             get_finance_transactions / get_stock_snapshot /   │
│             get_ad_performance / get_daily_summary            │
│                                                               │
│  固定 SQL → PostgreSQL                                        │
└──────────────────────────────────────────────────────────────┘
```

---

### 5.1 AgentState 设计

```python
class AgentState(TypedDict):
    # ── 对话 ──
    messages: list              # LangGraph 消息历史 (HumanMsg, AIMsg, ToolMsg)
    user_query: str             # 本轮原始问题

    # ── 意图 & 实体 ──
    intent: str                 # "lookup" | "anomaly" | "advice" | "chat"
    entities: dict              # {date_range, sku_ids, metrics, store_id, ...}

    # ── 工具执行 ──
    tool_results: dict          # {tool_name: {data: [...], row_count: N, error?: str}}

    # ── 分析流水线 ──
    analysis: str               # 数据解读文本
    anomalies: list[dict]       # [{type, severity, detail, attribution, sku}]
    suggestions: list[str]      # 可执行运营建议

    # ── 配置 & 最终输出 ──
    config: dict                # metrics.yaml 加载结果（业务口径 + 异常规则）
    final_answer: str           # 给用户的最终回答
    error: str                  # 全局错误标记（任何节点异常不崩，写这里）
```

**关键变化**（相比初版设计）：
- 新增 `messages`：LangGraph + Function Calling 的必要字段，LLM 通过 messages 历史感知上下文
- 新增 `entities`：从 `understand` 节点提取的结构化实体，供 `plan` 节点填入 tool 参数
- 新增 `config`：从 `metrics.yaml` 加载，随 state 流经全链路，各节点按需读取
- 新增 `error`：全局错误标记，节点异常不抛，写入此字段，后续节点自行处理
- 移除 `selected_tools` + `tool_params`：Function Calling 下由 LLM 在 `plan` 节点直接生成 `tool_calls`，不需要单独存

---

### 5.2 状态机流程（7 节点 + 条件路由）

```
                        START
                          │
                          ▼
               ┌────────────────────┐
               │  ① understand      │  LLM: 意图分类 + 实体提取
               │  model: simple      │  输出 intent + entities
               └────────┬───────────┘
                        │
              ┌─────────┴──────────┐
              │ route_after_       │  代码判断
              │ understand         │
              └─────────┬──────────┘
            chat        │        lookup / anomaly / advice
            │           │
            ▼           ▼
┌────────────────┐  ┌──────────────────────────────┐
│  ⑦ respond     │  │  ② plan                       │
│  (直接回答)     │  │  LLM + Function Calling       │
│                │  │  model: full + bind_tools()    │
└────────────────┘  │  输出 AIMessage.tool_calls      │
                    └──────────────┬───────────────┘
                                   │
                         ┌─────────┴──────────┐
                         │ route_after_plan   │  代码判断
                         └─────────┬──────────┘
                    无 tool_calls  │  有 tool_calls
                         │         │
                         ▼         ▼
              ┌──────────────┐ ┌──────────────────────────┐
              │ ⑦ respond    │ │  ③ call_tools             │
              │ (无需数据)    │ │  代码: MCP streamable-http │
              └──────────────┘ │  并行 asyncio.gather       │
                               │  输出 tool_results         │
                               └────────────┬─────────────┘
                                            │
                                            ▼
                               ┌──────────────────────────┐
                               │  ④ analyze                │
                               │  LLM: 数据解读 + 交叉验证  │
                               │  model: full              │
                               │  输出 analysis            │
                               └────────────┬─────────────┘
                                            │
                                  ┌─────────┴──────────┐
                                  │ route_after_       │  代码判断
                                  │ analyze            │
                                  └─────────┬──────────┘
                              lookup       │     anomaly / advice
                              │            │
                              ▼            ▼
                 ┌────────────────┐  ┌──────────────────────┐
                 │ ⑦ respond      │  │  ⑤ detect             │
                 │ (跳过检测/建议)  │  │  代码规则扫描 + LLM   │
                 └────────────────┘  │  归因                 │
                                     │  输出 anomalies       │
                                     └──────────┬───────────┘
                                                │
                                                ▼
                                     ┌──────────────────────┐
                                     │  ⑥ suggest            │
                                     │  LLM: 可执行运营建议   │
                                     │  model: full          │
                                     │  输出 suggestions     │
                                     └──────────┬───────────┘
                                                │
                                                ▼
                                     ┌──────────────────────┐
                                     │  ⑦ respond            │
                                     │  LLM: 组装最终回答     │
                                     │  model: simple        │
                                     │  输出 final_answer    │
                                     └──────────┬───────────┘
                                                │
                                                ▼
                                               END
```

---

### 5.3 7 节点职责明细

| # | 节点 | 谁做 | 模型 | 输入 | 输出 | 核心逻辑 |
|---|------|------|------|------|------|----------|
| ① | **understand** | LLM | simple | user_query | intent + entities | 意图分类（4 类）+ 提取日期/SKU/指标/店铺等实体。只做理解，不选工具 |
| ② | **plan** | LLM + FC | full + tools | messages + entities | AIMessage(tool_calls) | 拿到 7 个 tool 定义，LLM 自行决定调哪些、填什么参数。原生 Function Calling |
| ③ | **call_tools** | 代码 | — | AIMessage.tool_calls | tool_results | MCP streamable-http 客户端，`asyncio.gather` 并行执行。单工具失败不崩，标记 error 字段 |
| ④ | **analyze** | LLM | full | tool_results + config | analysis | 解读数据：趋势/对比/关联。对 ⑦ 的派生数据主动提醒交叉验证。将疑似异常点内嵌到分析中 |
| ⑤ | **detect** | 代码 + LLM | full | tool_results + config + analysis | anomalies | **代码层**：从 config 读异常规则（阈值/环比/同比），扫描数据打标记。**LLM 层**：对标记的异常做归因推理。规则读不到就跳过，框架不崩 |
| ⑥ | **suggest** | LLM | full | anomalies + analysis + config | suggestions | 基于异常 + 数据特征生成可执行建议（具体到 SKU/类目/操作） |
| ⑦ | **respond** | LLM | simple | 全部上下文 | final_answer | 组装最终回答：数据支撑 + 分析结论 + 异常归因 + 建议。面向运营人员，清晰结构化 |

**模型说明**：
- `simple` = DeepSeek V4 Pro（轻量推理，用于分类和格式化输出）
- `full` = DeepSeek V4 Pro（完整推理能力，用于分析/规划/归因）
- 当前阶段使用同一模型，后续可分流：simple → 小模型，full → 强模型
- `full + tools` = full 模型实例 `.bind_tools(tool_definitions)`，仅在 `plan` 节点使用

---

### 5.4 路由策略

```
                        条件                           →  下一节点
─────────────────────────────────────────────────────────────────────
understand 后:  intent == "chat"                        →  respond
                intent != "chat"                        →  plan

plan 后:       最后一条 AIMessage 无 tool_calls          →  respond
               最后一条 AIMessage 有 tool_calls          →  call_tools

call_tools 后: 始终                                     →  analyze

analyze 后:    intent == "lookup"                       →  respond
               intent ∈ {anomaly, advice}               →  detect
```

**路由逻辑全部在代码层做（`route_after_*` 函数）**，不用 LLM 做路由判断——确定性强，零延迟。

**短路说明**：
- 闲聊（"你好"、"你是谁"）→ `understand` → `respond`，只过 2 个节点
- 简单查数（"最近 7 天卖了多少"）→ 全链路但 `analyze` 后跳到 `respond`，过 5 个节点
- 异常分析 / 运营建议 → 全链路 7 个节点

---

### 5.5 MCP streamable-http 集成

#### 启动方式

```bash
# 终端 1: MCP Server（独立进程）
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 MCP_PORT=8000 \
  python -m src.mcp_server.server

# 终端 2: Agent
python -m src.agent.run
```

#### mcp_client.py 设计

```python
"""MCP streamable-http 客户端封装。"""
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

class MCPClient:
    """管理 MCP 连接生命周期，提供 list_tools / call_tool。"""

    def __init__(self, url: str = "http://127.0.0.1:8000/mcp"):
        self.url = url
        self._session: ClientSession | None = None
        self._tools: list[dict] = []        # 缓存的工具 schema

    async def connect(self) -> list[dict]:
        """建立连接 + initialize + list_tools，返回工具列表。"""
        ...

    async def call_tool(self, name: str, args: dict) -> dict:
        """调单个工具，返回 {data, row_count, error?}。"""
        ...

    async def call_tools_parallel(self, calls: list[dict]) -> dict[str, dict]:
        """asyncio.gather 并行调多个工具。"""
        ...

    async def close(self):
        ...

# 模块级单例
_client: MCPClient | None = None

async def get_client() -> MCPClient:
    ...
```

#### tool_adapter.py 设计

```
MCP tool schema (list_tools 返回)        OpenAI function calling 格式
─────────────────────────────────        ──────────────────────────
{                                         {
  "name": "get_postings",                   "type": "function",
  "description": "...",                     "function": {
  "inputSchema": {                            "name": "get_postings",
    "type": "object",                         "description": "...",
    "properties": {                           "parameters": {
      "date_start": {"type": "string"},         "type": "object",
      "date_end":   {"type": "string"},         "properties": { ... },
      "status":     {"type": "string"},         "required": ["date_start", "date_end"]
      ...                                      }
    },                                       }
    "required": ["date_start", "date_end"]  }
  }                                       }
}
```

- `list_tools()` 从 MCP Server 拿到 7 个 tool schema（inputSchema 即 JSON Schema）
- `adapt_tools()` 把 MCP schema 转为 OpenAI function 格式
- 绑到 `ChatOpenAI.bind_tools(adapted_tools)` 即可用

---

### 5.6 LLM 配置（DeepSeek V4 Pro）

```python
# src/agent/graph.py

from langchain_openai import ChatOpenAI

# 两个实例，同一模型，后续可分流
simple_llm = ChatOpenAI(
    model="deepseek-chat",          # DeepSeek V4 Pro 的模型 ID
    base_url="https://api.deepseek.com/v1",  # OpenAI 兼容 endpoint
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0.1,                # 分类/格式化 → 低温度
)

full_llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0.3,                # 分析/推理 → 稍高温度
)

# plan 节点专用：模型 + 工具绑定
def get_plan_llm(tool_definitions: list[dict]):
    return full_llm.bind_tools(tool_definitions)
```

**注意**：DeepSeek 的 model ID 和 base_url 以实际 API 文档为准，通过环境变量注入，不硬编码。

---

### 5.7 状态机实现骨架

```python
# src/agent/graph.py

from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("understand", understand_node)
    builder.add_node("plan", plan_node)
    builder.add_node("call_tools", call_tools_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("detect", detect_node)
    builder.add_node("suggest", suggest_node)
    builder.add_node("respond", respond_node)

    # 入口
    builder.add_edge(START, "understand")

    # 条件路由
    builder.add_conditional_edges("understand", route_after_understand, {
        "respond": "respond",
        "plan": "plan",
    })
    builder.add_conditional_edges("plan", route_after_plan, {
        "respond": "respond",
        "call_tools": "call_tools",
    })
    builder.add_edge("call_tools", "analyze")
    builder.add_conditional_edges("analyze", route_after_analyze, {
        "respond": "respond",
        "detect": "detect",
    })
    builder.add_edge("detect", "suggest")
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
✅ 店铺维度对比                  → 所有工具均传 store_id
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

## 7. 配置文件设计

### 7.1 加载方式

```python
# src/agent/config_loader.py

import yaml
from pathlib import Path

def load_config() -> dict:
    """加载 metrics.yaml，返回 dict。加载失败返回空配置，不崩。"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "metrics.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}   # 容错：配置加载失败，Agent 仍可运行
```

- 在 `graph.py` 构建 graph 之前调用一次，写入初始 state
- 配置随 AgentState 流经所有节点

### 7.2 `config/metrics.yaml` 结构

```yaml
# ── 业务口径（注入 analyze / respond 的 prompt）──
metrics:
  动销率:
    formula: "有订单SKU数 / 总SKU数 × 100%"
    warning_threshold: 30
    description: "反映库存周转健康度"

  广告DRR:
    formula: "广告花费 / 广告带来销售额 × 100%"
    warning_threshold: 30
    description: "广告投入产出比"

  转化率:
    formula: "订单数 / 点击数 × 100%"
    warning_threshold: 1.5

# ── 异常规则（detect 节点的代码扫描依据）──
# ⚠️ 当前阈值为占位值，后续基于真实数据调优，改 yaml 不动代码
anomaly_rules:
  订单取消率:
    type: "环比"
    threshold: 0.3          # 暂定：较前 7 天均值升 30% 触发
    window_days: 7

  退货率飙升:
    type: "环比"
    threshold: 0.5          # 暂定：较前 7 天均值升 50% 触发
    window_days: 7

  DRR异常:
    type: "阈值"
    threshold: 30           # 暂定：DRR > 30% 触发

  库存预警:
    type: "阈值"
    days_of_cover_low: 7    # 暂定：可售天数 < 7 触发
    days_of_cover_high: 90  # 暂定：可售天数 > 90 触发（滞销）
```

### 7.3 注入方式

- **启动时**：`config_loader.load_config()` → 写入 AgentState.config
- **analyze 节点**：prompt 中通过 `{config_metrics}` 注入业务口径，LLM 引用
- **detect 节点（代码层）**：从 `state["config"]["anomaly_rules"]` 读阈值，循环扫描
- **detect 节点（LLM 层）**：将规则标记的异常传给 LLM 做归因

---

## 8. Prompt 管理策略

全部 prompt 集中在 `src/agent/prompts.py`，按节点命名：

```python
# ── ① understand ──
UNDERSTAND_SYSTEM = """你是 OZON 电商数据分析助手的意图识别模块。
根据用户问题，判断意图并提取实体。

意图类型：
- lookup: 查数据、看报表、看趋势（"最近销量怎么样"）
- anomaly: 找异常、发现问题（"有没有哪个品卖得不好"）
- advice: 要建议、要优化方向（"怎么提升转化率"）
- chat: 闲聊、自我介绍、能力询问（"你能做什么"）

提取实体（能提就提，不确定就 null）：
- date_range: 时间范围（如 "last_7_days" / "2026-01-01 to 2026-01-07"）
- sku_ids: 涉及的 SKU ID 列表
- metrics: 涉及的指标（销量/退货率/利润/广告DRR...）
- store_id: 涉及的店铺

返回 JSON：
{{"intent": "lookup", "entities": {{"date_range": "last_7_days", "sku_ids": null, "metrics": ["订单量"], "store_id": null}}}}
"""

# ── ② plan ──
PLAN_SYSTEM = """你是 OZON 电商数据分析助手。根据用户问题和已提取的实体，决定调用哪些数据工具。

可用工具已通过 Function Calling 提供。关键规则：
1. 优先使用原始表工具（get_postings / get_returns / get_finance_transactions），get_daily_summary 作为辅助
2. 日期范围必填，最多 90 天
3. 交叉分析时尽可能并行调多个工具，减少往返
4. store_id 不传 = 全平台汇总，传了 = 单店铺
"""
# 注：plan 节点的工具列表从 MCP list_tools 动态获取，不需要硬编码描述

# ── ④ analyze ──
ANALYZE_PROMPT = """基于以下数据进行分析。

业务口径参考：
{config_metrics}

各工具返回数据：
{tool_results}

⚠️ get_daily_summary 是 ETL 派生数据，可能存在误差。如果关键结论依赖此数据，
请主动提醒用户可以交叉对比原始表（postings / finance_transactions / returns）验证。

请分析：
1. 核心指标概况（总体趋势）
2. 值得关注的维度（按 SKU / 店铺 / 类型拆解）
3. 数据间的关联（广告花费 vs 销量，库存 vs 动销率等）
4. 疑似异常点（数据明显偏离正常范围的情况）
"""

# ── ⑤ detect（归因部分）──
DETECT_ATTRIBUTION_PROMPT = """以下数据点被规则标记为疑似异常：

{anomalies}

相关数据：
{tool_results}

请对每个异常进行归因分析：
1. 可能的原因（数据侧 / 运营侧 / 外部因素）
2. 严重程度评估
3. 建议是否需要进一步排查

输出 JSON 数组，每个元素在原有 anomaly 基础上增加 attribution 字段。
"""

# ── ⑥ suggest ──
SUGGEST_PROMPT = """基于以下分析结果和异常发现，生成可执行的运营建议。

分析：
{analysis}

异常：
{anomalies}

业务口径：
{config_metrics}

建议要求：
- 具体到 SKU / 类目 / 店铺，不说空话
- 可执行（"调整广告出价"而不是"优化广告"）
- 区分紧急程度（立即处理 / 短期优化 / 长期关注）
"""

# ── ⑦ respond ──
RESPOND_SYSTEM = """你是 OZON 电商数据分析助手。将分析结果转化为运营人员能直接用的回答。

原则：
- 先说结论，再说细节
- 数据要具体（带数字、排名、百分比）
- 有异常说异常，有问题给建议
- 结构清晰，适当使用分段/列表
- 如果数据来自 get_daily_summary，标注"（概览数据，建议交叉验证）"
"""
```

---

## 9. 异常检测策略：框架完整，阈值后调

### 设计原则

| 环节 | 谁做 | 为什么 |
|---|---|---|
| **检测**（是否异常） | 代码规则（阈值 / 同比 / 环比） | 确定性、可复现、零幻觉 |
| **归因**（为什么异常） | LLM 推理 | 发挥模型推理能力，多维度交叉分析 |
| **建议**（怎么办） | LLM 生成 | 需要业务知识 + 自然语言表达 |

### "框架完整，阈值后调" 具体含义

**detect 节点的代码框架一次写好**：
- 从 `state["config"]["anomaly_rules"]` 遍历所有已配置规则
- 每种规则类型（环比/阈值）的扫描逻辑写好 → 订单取消率环比、退货率飙升、DRR 异常、库存预警
- 扫描结果结构化输出 → `[{type, severity, detail}]`
- 有标记结果 → 调 LLM 归因；无标记 → 跳过归因，返回空列表
- 无规则配置 → 整个检测层跳过，不崩

**阈值后调**：
- `metrics.yaml` 中的 `threshold` 值为**占位值**（基于经验拍脑袋，不是数据分析结果）
- 后续用真实数据跑几轮，观察什么值合理，改 yaml 即可生效
- 代码逻辑不依赖具体阈值——读到就扫描，读不到就跳过

---

## 10. 开发顺序（一次性搭框架）

### 文件清单（按构建顺序）

```
步骤 1: 基础设施
  src/agent/__init__.py
  src/agent/state.py           # AgentState TypedDict
  src/config/metrics.yaml      # 业务口径 + 异常规则
  src/agent/config_loader.py   # YAML → dict

步骤 2: MCP 客户端
  src/agent/mcp_client.py      # streamable-http 连接 + 工具调用
  src/agent/tool_adapter.py    # MCP schema → OpenAI function 格式

步骤 3: Prompt 模板
  src/agent/prompts.py         # 7 个 prompt 模板

步骤 4: 状态机
  src/agent/graph.py           # 7 节点 + 4 条条件路由 + 主循环

步骤 5: 命令行入口
  src/agent/run.py             # CLI 交互入口

步骤 6: 依赖更新
  requirements.txt             # + langchain-openai

未动:
  src/mcp_server/*             # 7 个工具已稳定，不动
```

### 构建顺序（同一次工作会话内）

| 阶段 | 内容 | 验证方式 |
|---|---|---|
| **A. 配置层** | `state.py` + `metrics.yaml` + `config_loader.py` | Python import 无报错，load_config 返回 dict |
| **B. MCP 客户端** | `mcp_client.py` + `tool_adapter.py` | MCP Server 启动后，connect → list_tools 拿到 7 个工具；call_tool 返回数据 |
| **C. Prompts** | `prompts.py` | 各模板 `.format()` 无 KeyError |
| **D. 状态机** | `graph.py`（7 节点 + 路由） | LangGraph 编译通过，单步调试每个节点 |
| **E. 端到端** | `run.py` + 完整链路 | 输入问题 → Agent 输出分析结果 |
| **F. 验收** | 首条链路验收（见 Section 11） | 输出准确、可复现 |

---

## 11. 首条链路验收标准

### 验收场景 1：简单查数

输入：
> "最近 7 天订单量怎么样？"

Agent 行为：
1. ① understand → intent=lookup, entities={date_range: "last_7_days"}
2. ② plan → tool_calls: [get_postings(date_start, date_end)]
3. ③ call_tools → 执行 get_postings，拿到数据
4. ④ analyze → 解读数据
5. (route: lookup → respond，跳过 detect/suggest)
6. ⑦ respond → 输出：订单量、日均、趋势、"需要退货数据的话我可以查"

### 验收场景 2：交叉分析 + 异常 + 建议

输入：
> "最近 7 天退货率最高的 3 个 SKU 是哪些？退货原因是什么？"

Agent 行为：
1. ① understand → intent=anomaly
2. ② plan → tool_calls: [get_returns(...), get_products()]（并行 2 个工具）
3. ③ call_tools → 并行执行，收集结果
4. ④ analyze → 退货率排名、退货原因分布、交叉引用商品名
5. ⑤ detect → 规则扫描（如有退货率飙升规则且触发 → LLM 归因）
6. ⑥ suggest → 针对高退货率 SKU 给出建议（如"检查商品描述是否准确"）
7. ⑦ respond → 排名 + 原因 + 建议，带具体数据

### 验收标准

- [ ] 端到端链路不抛异常（MCP Server 不可用除外）
- [ ] intent 分类正确率 > 80%（人工判断 10 条）
- [ ] Function Calling 选工具准确率 > 80%（预期工具被选中）
- [ ] 单工具调用失败不导致整个链路崩溃
- [ ] 回答中带具体数据（数字/排名），不空洞
- [ ] get_daily_summary 数据出现时标注了可靠性警告

Phase 1 完成标志：两条验收场景稳定跑通。

---

## 12. 依赖安装

```bash
source venv/Scripts/activate
pip install mcp langgraph langgraph-checkpoint asyncpg pyyaml python-dotenv openai langchain-openai
pip freeze > requirements.txt
```

**新增依赖**：
| 包 | 用途 |
|---|---|
| `langchain-openai` | ChatOpenAI 模型接入（DeepSeek 兼容 API）+ Function Calling 绑定 |

---

## 关键决策记录

| 决策 | 选什么 | 为什么不选另一个 |
|---|---|---|
| **不做 Text-to-SQL** | 语义化工具 + 固定 SQL | 准确率 100%、安全边界清晰 |
| **原始表优先，派生表辅助** | 看板数据只用 sku_daily_summary 做概览 | ETL 可能有误差，关键结论需交叉验证 |
| **工具数量不设限** | 7 个，按数据源拆分 | 充分利用数据库，不强行合并 |
| **SQL 只透传，不聚合** | SELECT + WHERE，不定义口径 | 口径交给 Agent + 配置文件 |
| **不用 RAG** | 配置文件注入业务口径 | 仅二十来条口径，RAG 过度工程 |
| **Agent 不碰 SQL** | MCP 工具封装 | 安全边界清晰，Agent 只管"调哪个工具+传什么参" |
| **规则检测 + LLM 归因** | 混合策略 | 检测要可靠（规则），归因要智能（LLM），各取所长 |
| **MCP streamable-http** | Agent 通过 HTTP 连接 MCP Server | 解耦：MCP Server 和 Agent 独立演进、独立部署，和外部客户端（Claude Code 等）一致的接口 |
| **Function Calling** | LLM 原生 tool_calls 选工具 | 比 Text-to-JSON 格式更可靠、不会飘；LangGraph 原生支持 AIMessage.tool_calls |
| **Detect 框架完整，阈值后调** | 规则扫描代码一次写好，阈值占位 | 代码逻辑不依赖具体数字，后续调 yaml 即可；避免"先写死数字后面再重构" |
| **DeepSeek V4 Pro** | langchain-openai 的 ChatOpenAI 接入 | OpenAI 兼容 API，切换成本低；后续如需换模型只改配置 |
| **7 节点全搭** | 不拆分周末 | 架子完整，后续只做填充和调优；避免"先简化后面再加"导致的返工 |
