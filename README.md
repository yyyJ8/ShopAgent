# OZON 电商数据分析 Agent

基于 **LangGraph + FastMCP + PostgreSQL** 的多店铺电商数据分析 Agent。

> 自然语言提问 → Agent 选工具 → 拿数据 → 交叉分析 → 异常检测 → 运营建议，完整闭环。

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2+-blue)](https://langchain-ai.github.io/langgraph/)
[![FastMCP](https://img.shields.io/badge/FastMCP-1.0+-orange)](https://github.com/jlowin/fastmcp)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?logo=postgresql)](https://www.postgresql.org/)
[![DeepSeek](https://img.shields.io/badge/DeepSeek-V4_Pro-green)](https://www.deepseek.com/)

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 .env
# DB_HOST=192.168.x.x
# DB_PORT=5432
# DB_NAME=ai_application
# DB_USER=your_user
# DB_PASSWORD=your_password
# DEEPSEEK_API_KEY=sk-your-key    ← 无引号

# 3. 启动 MCP Server（streamable-http 模式）
MCP_TRANSPORT=http python -m src.mcp_server.server

# 4. 启动 Agent CLI（另一个终端）
python -m src.agent.run
```

---

## 架构

```
用户自然语言
    ↓
┌─────────────────────────────────────────────┐
│ LangGraph Agent ✅                            │
│                                               │
│  START → ① understand → ② plan → ③ call_tools │
│              ↓                        ↓       │
│          ⑦ respond  ←  ⑥ suggest  ←  ⑤ detect │
│              ↑              ↑          ↑      │
│              └── ④ analyze ─┴──────────┘      │
│                                               │
│  路由：chat → respond（短路）                   │
│        lookup → respond（跳过 detect/suggest）  │
│        anomaly/advice → 全链路                  │
│                                               │
│  MCP Client (streamable-http)                 │
│    list_tools() → 7 个工具 schema              │
│    call_tool(name, args) → 执行查询            │
└──────────────────┬──────────────────────────┘
                   │ HTTP
                   ▼
┌─────────────────────────────────────────────┐
│ MCP Server: ozon-data ✅                      │
│  ① get_products              商品主数据       │
│  ② get_postings              订单/发货        │
│  ③ get_returns               退货数据         │
│  ④ get_finance_transactions  财务流水         │
│  ⑤ get_stock_snapshot         实时库存        │
│  ⑥ get_ad_performance         广告表现        │
│  ⑦ get_daily_summary          日汇总 ⚠️       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
PostgreSQL（固定参数化 SQL，2 店铺 / 81+ 商品）
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | DeepSeek V4 Pro | OpenAI 兼容 API，Function Calling |
| 编排 | LangGraph 1.2+ | StateGraph + 7 节点 + 4 条件路由 + MemorySaver |
| MCP | FastMCP | 7 个语义化数据工具，stdio/sse/streamable-http 三种传输 |
| 数据库 | PostgreSQL + asyncpg | 异步连接池，json/jsonb 自动解析 |
| 配置 | python-dotenv + PyYAML | .env 密钥不进仓库，metrics.yaml 业务口径 |
| 入口 | CLI / Streamlit | 先命令行交互，后续加 UI |

---

## MCP 工具

| 工具 | 数据源 | 可靠度 | 说明 |
|------|--------|--------|------|
| ① get_products | products 原始表 | ⭐⭐⭐ 高 | 商品主数据，按 SKU/状态/类目/店铺筛选 |
| ② get_postings | postings 原始表 | ⭐⭐⭐ 高 | 订单/发货，含 products jsonb 明细 |
| ③ get_returns | returns 原始表 | ⭐⭐⭐ 高 | 退货记录 + 退货原因（俄文） |
| ④ get_finance_transactions | finance_transactions 原始表 | ⭐⭐⭐ 高 | 操作级财务流水，13 种 operation_type |
| ⑤ get_stock_snapshot | stocks 原始表 | ⭐⭐⭐ 高 | 实时库存（present/reserved × FBO/FBS） |
| ⑥ get_ad_performance | ad_sku_daily_stats | ⭐⭐ 中 | SKU × 活动 × 天广告表现 |
| ⑦ get_daily_summary | sku_daily_summary ETL | ⭐ 低-中 | SKU 日损益汇总，关键结论需交叉验证原始表 |

所有工具支持可选 `store_id`，不传 = 全平台汇总。

---

## Agent 节点（7 节点 + 4 条件路由）

| 节点 | 谁做 | 模型 | 说明 |
|------|------|------|------|
| ① understand | LLM | simple | 意图分类（lookup/anomaly/advice/chat）+ 实体提取 |
| ② plan | LLM + FC | full + tools | Function Calling 选工具 + 填参数 |
| ③ call_tools | 代码 | — | MCP streamable-http 并行执行，单工具失败不崩 |
| ④ analyze | LLM | full | 数据解读 + 交叉验证 + 每日汇总可靠性警告 |
| ⑤ detect | 代码 + LLM | full | 规则扫描（阈值/环比）+ LLM 归因 |
| ⑥ suggest | LLM | full | 基于异常 + 数据生成可执行运营建议 |
| ⑦ respond | LLM | simple | 组装最终回答，面向运营人员 |

### 路由策略

```
chat 意图       → understand → respond（2 节点）
lookup 意图     → 跳过 detect/suggest（5 节点）
anomaly/advice  → 全链路（7 节点）
plan 无 tool_calls → respond（无需调工具时直接应答）
```

---

## 项目结构

```
├── src/
│   ├── mcp_server/         # MCP Server ✅
│   │   ├── db.py           #   PostgreSQL 连接池 + 7 条固定参数化 SQL
│   │   ├── tools.py        #   7 工具函数（参数校验 + 格式化返回 + 错误处理）
│   │   └── server.py       #   FastMCP 入口（3 种传输模式）
│   ├── agent/              # LangGraph Agent ✅
│   │   ├── state.py        #   AgentState TypedDict（11 字段）
│   │   ├── prompts.py      #   7 个节点 prompt 模板
│   │   ├── graph.py        #   状态机（7 节点 + 4 条件路由）
│   │   ├── mcp_client.py   #   MCP streamable-http 客户端封装
│   │   ├── tool_adapter.py #   MCP schema → OpenAI function 格式
│   │   ├── config_loader.py#   metrics.yaml 加载
│   │   └── run.py          #   CLI 交互入口
│   └── config/             # 业务口径配置
│       └── metrics.yaml    #   动销率/转化率/异常阈值
├── scripts/
│   └── explore_db.py       # 数据库结构探索工具
├── tests/
│   ├── test_mcp_tools.py   # MCP 工具验证（全工具 + 参数校验）
│   └── test_e2e_agent.py   # Agent 端到端测试
├── plan/
│   ├── ROADMAP.md          # 总体规划 + 里程碑
│   └── phase-1.md          # Phase 1 详细设计
├── requirements.txt
├── .env                    # 数据库连接串 + API Key（不进仓库）
└── README.md
```

---

## 关键决策记录

| 决策 | 选什么 | 为什么不选另一个 |
|------|--------|------------------|
| **不做 Text-to-SQL** | 语义化工具 + 固定 SQL | 准确率 100%、安全边界清晰 |
| **原始表优先，派生表辅助** | 6 个原始表工具 + 1 个 ETL 工具 | ETL 可能有误差，关键结论需交叉验证 |
| **Agent 不碰 SQL** | MCP 工具封装 | 安全边界清晰，Agent 只管"调哪个工具 + 传什么参" |
| **MCP streamable-http** | 双进程 HTTP 通信 | Agent 和 MCP Server 独立演进、独立部署 |
| **Function Calling** | LLM 原生 tool_calls | 比 Text-to-JSON 格式更可靠，不会解析错误 |
| **7 节点全搭** | 一次写好完整框架 | 后续只需调阈值和 prompt，不返工 |
| **规则检测 + LLM 归因** | 混合策略 | 检测要可靠（规则），归因要智能（LLM） |
| **DeepSeek V4 Pro** | langchain-openai ChatOpenAI 接入 | OpenAI 兼容 API，切换成本低 |

---

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 MCP | 7 个语义化数据工具 + store_id 过滤 + 3 种传输模式 | ✅ |
| Phase 1 Agent | 7 节点全框架 + streamable-http 通信 + Function Calling | ✅ |
| Phase 1 后续 | detect 规则阈值调优 + prompt 迭代 | ⏭ |
| Phase 2 | 评测闭环（20 道测试题 + 准确率指标） | ⏭ |
