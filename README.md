# OZON 电商数据分析 Agent

基于 **LangGraph + FastMCP + PostgreSQL** 的多店铺电商数据分析 Agent。

> 自然语言提问 → Agent 选工具 → 拿数据 → 交叉分析 → 异常检测 → 运营建议，完整闭环。

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-blue)](https://langchain-ai.github.io/langgraph/)
[![FastMCP](https://img.shields.io/badge/FastMCP-1.0+-orange)](https://github.com/jlowin/fastmcp)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?logo=postgresql)](https://www.postgresql.org/)
[![DeepSeek](https://img.shields.io/badge/DeepSeek-V4_Pro-green)](https://www.deepseek.com/)

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（编辑 .env 填入数据库连接串 + API Key）
# DB_HOST=192.168.x.x
# DB_PORT=5432
# DB_NAME=ai_application
# DB_USER=your_user
# DB_PASSWORD=your_password
# DEEPSEEK_API_KEY=sk-your-key

# 3. 探索数据库结构
python scripts/explore_db.py
# → scripts/output/db_ozon_YYYYMMDD.txt

# 4. 运行 MCP 工具测试
python tests/test_mcp_tools.py

# 5. 启动 MCP Server（SSE 模式，开发调试）
MCP_TRANSPORT=sse python -m src.mcp_server.server
# → http://127.0.0.1:8000/sse

# 6. 启动 MCP Server（stdio 模式，Agent 集成）
python -m src.mcp_server.server
```

---

## 架构

```
用户自然语言
    ↓
LangGraph Agent（下一步）
  ├── 理解问题（意图识别 + 参数提取）
  ├── 选择工具 + 传参（路由策略）
  ├── 调用 MCP 工具 → 拿数据
  ├── 交叉分析（多工具数据拼合解读）
  ├── 异常检测（规则打标记 + LLM 归因）
  └── 运营建议（可执行建议）
    ↓
MCP Server: ozon-data（已完成 ✅）
  ├── ① get_products              商品主数据
  ├── ② get_postings              订单/发货（原始销售数据）
  ├── ③ get_returns               退货数据（含退货原因）
  ├── ④ get_finance_transactions  财务流水
  ├── ⑤ get_stock_snapshot         实时库存
  ├── ⑥ get_ad_performance         广告表现
  └── ⑦ get_daily_summary          日汇总（⚠️ 派生数据）
    ↓
PostgreSQL（固定参数化 SQL，2 店铺 / 81+ 商品）
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | DeepSeek V4 Pro | OpenAI 兼容 API |
| 编排 | LangGraph | StateGraph + 条件路由 + MemorySaver |
| MCP | FastMCP | 7 个语义化数据工具，3 种传输模式 |
| 数据库 | PostgreSQL + asyncpg | 异步连接池，json/jsonb 自动解析 |
| 配置 | python-dotenv + PyYAML | .env 密钥不进仓库，metrics.yaml 业务口径 |
| 入口 | 命令行（→ Streamlit） | Phase 1 先命令行，后续加 UI |

---

## MCP 工具

### 7 个语义化数据工具

| 工具 | 数据源 | 可靠度 | 说明 |
|------|--------|--------|------|
| ① get_products | products 原始表 | ⭐⭐⭐ 高 | 商品主数据，支持按 SKU/状态/类目/店铺筛选 |
| ② get_postings | postings 原始表 | ⭐⭐⭐ 高 | 订单/发货，含 products jsonb 明细 |
| ③ get_returns | returns 原始表 | ⭐⭐⭐ 高 | 退货记录 + 退货原因（俄文） |
| ④ get_finance_transactions | finance_transactions 原始表 | ⭐⭐⭐ 高 | 操作级财务流水，13 种 operation_type |
| ⑤ get_stock_snapshot | stocks 原始表 | ⭐⭐⭐ 高 | 实时库存（present/reserved × FBO/FBS） |
| ⑥ get_ad_performance | ad_sku_daily_stats | ⭐⭐ 中 | SKU × 活动 × 天广告表现（OZON API 同步） |
| ⑦ get_daily_summary | sku_daily_summary ETL 派生 | ⭐ 低-中 | SKU 日损益汇总，key insights 需交叉验证原始表 |

### 核心设计原则

- **固定 SQL + 参数化**：`WHERE date BETWEEN $1 AND $2`，Agent 碰不到 SQL，零注入风险
- **store_id 过滤**：所有工具支持可选 `store_id`，不传 = 全平台，传了 = 单店铺
- **LIMIT 保护**：默认 500 条，防止大数据量撑爆上下文
- **JSON 自动解析**：json/jsonb 列通过 asyncpg codec 自动解码为 Python dict/list

### 三种传输模式

```bash
# stdio（Agent 集成，默认）
python -m src.mcp_server.server

# SSE（开发调试）
MCP_TRANSPORT=sse python -m src.mcp_server.server

# Streamable HTTP（生产部署）
MCP_TRANSPORT=http python -m src.mcp_server.server
```

端口和地址可通过环境变量修改：`MCP_HOST=0.0.0.0 MCP_PORT=9000`

---

## 分析能力（三层递进）

| 层级 | 能力 | 示例 |
|------|------|------|
| **查数层** | 自然语言 → 工具调用 → 数据解读 | "最近 7 天哪个品卖得最好？" |
| **交叉分析层** | 多工具并行 + 数据拼合 | "卖得好的品退货率高不高？广告投产比如何？" |
| **异常检测层** | 规则扫描 + LLM 归因 + 建议 | "有没有哪个 SKU 利润率突然恶化？原因是什么？" |

### 店铺维度

| 维度 | 示例 |
|------|------|
| 单店铺内部 | "店铺 1 最近 7 天销量/利润/退货率？" |
| 店铺间对比 | "店铺 1 vs 店铺 2 的利润率/广告投产比？" |
| 全平台汇总 | "所有店铺加起来这个月卖了多少？" |

---

## 项目结构

```
├── src/
│   ├── mcp_server/         # MCP Server（✅ 已完成）
│   │   ├── db.py           #   PostgreSQL 连接池 + 7 条固定参数化 SQL
│   │   ├── tools.py        #   7 工具函数（参数校验 + 格式化返回 + 错误处理）
│   │   └── server.py       #   FastMCP 入口（3 种传输模式）
│   ├── agent/              # LangGraph Agent（⏭ 下一步）
│   │   ├── state.py        #   AgentState 类型定义
│   │   ├── prompts.py      #   Prompt 模板 + 工具描述
│   │   └── graph.py        #   状态机（7 节点 + 条件路由）
│   └── config/             # 业务口径配置
│       └── metrics.yaml    #   动销率/转化率/异常阈值
├── scripts/
│   └── explore_db.py       # 数据库结构探索工具
├── tests/
│   └── test_mcp_tools.py   # MCP 工具验证（全工具 + 参数校验）
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
| **工具数量不设限** | 7 个，按数据源拆分 | 充分利用数据库，不强行合并 |
| **SQL 只透传，不聚合** | SELECT + WHERE + ORDER BY | 口径交给 Agent + 配置文件 |
| **Agent 不碰 SQL** | MCP 工具封装 | 安全边界清晰，Agent 只管"调哪个工具 + 传什么参" |
| **规则检测 + LLM 归因** | 混合策略 | 检测要可靠（规则），归因要智能（LLM） |

---

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 Week 1 | MCP Server + 7 个工具 + store_id 过滤 | ✅ |
| Phase 1 Week 2 | LangGraph Agent 骨架（状态机 + 端到端跑通） | ⏭ |
| Phase 1 Week 3 | 异常检测 + 运营建议 + 路由策略 | ⏭ |
| Phase 1 Week 4 | 配置文件注入 + 多轮对话 + 打磨 | ⏭ |
| Phase 2 | 评测闭环（20 道测试题 + 准确率指标） | ⏭ |
