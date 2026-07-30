# OZON 电商数据分析 Agent

基于 **LangGraph + FastMCP + PostgreSQL** 的多店铺电商数据分析 Agent。

> 自然语言提问 → Agent 选工具 → 拿数据 → 交叉分析 → 异常检测 → 运营建议，完整闭环。

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2+-blue)](https://langchain-ai.github.io/langgraph/)
[![FastMCP](https://img.shields.io/badge/FastMCP-1.0+-orange)](https://github.com/jlowin/fastmcp)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?logo=postgresql)](https://www.postgresql.org/)
[![DeepSeek](https://img.shields.io/badge/DeepSeek-V4_Flash-green)](https://www.deepseek.com/)

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
# DEEPSEEK_API_KEY=sk-your-key

# 3. 一键启动（Git Bash）
bash start.sh

# 4. 或手动启动两个终端
# 终端 1 — MCP Server
MCP_TRANSPORT=streamable-http MCP_PORT=8000 python -m src.mcp_server.server
# 终端 2 — Gradio Web 界面
python -m src.gradio_app
# CLI 模式（可选）
python -m src.agent.run
```

浏览器打开 `http://localhost:8501` 即可使用。

---

## 架构

```
用户 → Gradio Web UI
         ↓  thread_id（SqliteSaver 持久化）
     LangGraph Agent（8 节点 + 5 意图路由）
         ↓  streamable-http
     MCP Server（FastMCP · 8 工具）
         ↓  asyncpg 连接池
     PostgreSQL（9 原始表 + 1 派生表）
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | DeepSeek V4 Flash | OpenAI 兼容 API，streaming + Function Calling |
| 编排 | LangGraph 1.2+ | StateGraph + 8 节点 + 5 条件路由 + 数据完整性环 |
| 持久化 | SqliteSaver（自实现） | 标准库 sqlite3，对话历史重启不丢 |
| MCP | FastMCP | 8 个语义化数据工具，streamable-http 传输 |
| 数据库 | PostgreSQL + asyncpg | 异步连接池，json/jsonb 自动解析 |
| 配置 | python-dotenv + PyYAML | .env 密钥不进仓库，metrics.yaml 业务口径 |
| UI | Gradio 6 | gr.Blocks + 进度树 + 流式输出 |
| 优化 | 数据摘要层 | analyze/detect 入参压缩 85%，token 成本大幅降低 |

---

## MCP 工具

| 工具 | 数据源 | 说明 |
|------|--------|------|
| 1 get_products | products 原始表 | 商品主数据，按 SKU/货号(offer_id)/条形码/状态/类目/店铺筛选 |
| 2 get_postings | postings 原始表 | 订单/发货，含 products jsonb 明细 |
| 3 get_returns | returns 原始表 | 退货记录 + 退货原因（俄文），SKU 列名为 `sku` |
| 4 get_finance_transactions | finance_transactions 原始表 | 操作级财务流水，13 种 operation_type |
| 5 get_stock_snapshot | stocks 原始表 | 实时库存（present/reserved x FBO/FBS） |
| 6 get_ad_performance | ad_sku_daily_stats | SKU x 计划 x 天广告表现，含 ctr/drr_total/spend/sold_units |
| 7 get_ad_campaign_stats | ad_daily_stats | 计划级日统计，含 orders_count/orders_sum，无 SKU 粒度 |
| 8 get_daily_summary | sku_daily_summary ETL | SKU 日损益汇总，关键结论需交叉验证原始表 |

所有工具支持可选 `store_id`，不传 = 全平台汇总。

---

## Agent 节点（8 节点 + 5 意图路由）

| 节点 | 谁做 | 模型 | 说明 |
|------|------|------|------|
| 1 understand | LLM | simple | 意图分类 + 实体提取 + 对话历史代词解析 |
| 2 plan | LLM + FC | full + tools | Function Calling 选工具 + 填参数 |
| 3 call_tools | 代码 | — | MCP 并行调用，补调去重 |
| 4 data_check | 代码 | — | 异常规则数据源覆盖检查，≤2 轮补调，超限熔断降级 |
| 5 analyze | LLM | full | 数据解读 + 交叉验证（入参经过摘要压缩） |
| 6 detect | 代码 + LLM | full | 6 条阈值规则扫描 + LLM 归因（异常 SKU 上下文提取） |
| 7 suggest | LLM | full | 基于异常 + 数据生成可执行运营建议 |
| 8 respond | LLM | simple | 组装最终回答；clarify 意图时主动反问引导 |

### 意图路由

| 意图 | 路径 | 节点数 | 说明 |
|------|------|--------|------|
| chat | understand → respond | 2 | 闲聊直接应答 |
| clarify | understand → respond | 2 | 问题太模糊，反问引导用户补充 |
| lookup | understand → plan → call_tools → analyze → respond | 5 | 查数据，跳过检测 |
| anomaly | 全链路 + 数据完整性环 | 8 | 找异常，缺数据自动补调 |
| advice | 全链路 + 数据完整性环 | 8 | 要建议，先检测再生成 |
| plan 无 tool_calls | → respond | — | LLM 选择不调工具时直接应答 |

---

## 项目结构

```
├── src/
│   ├── mcp_server/              # MCP Server
│   │   ├── db.py                #   PostgreSQL 连接池 + 固定参数化 SQL
│   │   ├── tools.py             #   8 工具函数（参数校验 + 格式化返回）
│   │   └── server.py            #   FastMCP 入口（streamable-http）
│   ├── agent/                   # LangGraph Agent
│   │   ├── state.py             #   AgentState TypedDict
│   │   ├── prompts.py           #   7 个节点 prompt 模板
│   │   ├── graph.py             #   状态机（8 节点 + 5 意图路由）
│   │   ├── logger.py            #   节点级结构化日志
│   │   ├── mcp_client.py        #   MCP streamable-http 客户端
│   │   ├── tool_adapter.py      #   MCP schema → OpenAI function 格式
│   │   ├── config_loader.py     #   metrics.yaml 加载
│   │   ├── data_summarizer.py   #   数据摘要 + 异常上下文提取（Token 优化）
│   │   ├── sqlite_saver.py      #   标准库 sqlite3 实现的 LangGraph 持久化
│   │   └── run.py               #   CLI 交互入口
│   ├── gradio_app.py            # Gradio Web 聊天界面（进度树 + 流式输出）
│   └── config/
│       └── metrics.yaml         # 7 业务口径 + 6 异常检测规则
├── data/
│   ├── eval_questions.json      # 评测题库（21 题）
│   ├── eval_results.json        # 评测结果
│   └── checkpoints.db           # SqliteSaver 对话持久化文件
├── tests/
│   ├── test_mcp_tools.py        # MCP 工具验证
│   ├── test_e2e_agent.py        # Agent 端到端测试
│   ├── test_eval.py             # 评测运行脚本（20 题自动打分）
│   └── test_aggregation.py      # 聚合逻辑单元测试
├── plan/
│   ├── phase-1.md               # Phase 1 技术设计
│   └── phase-2.md               # Phase 2 规划与验收
├── EVAL_REPORT.md               # 评测报告（诊断 + 修复记录）
├── start.sh                     # 一键启动脚本
├── requirements.txt
├── .env
└── README.md
```

---

## 关键决策记录

| 决策 | 选什么 | 为什么不选另一个 |
|------|--------|------------------|
| **不做 Text-to-SQL** | 语义化工具 + 固定 SQL | 准确率 100%、安全边界清晰 |
| **原始表优先，派生表辅助** | 7 个原始表工具 + 1 个 ETL 工具 | ETL 可能有误差，关键结论需交叉验证 |
| **Agent 不碰 SQL** | MCP 工具封装 | Agent 只管"调哪个工具 + 传什么参" |
| **规则检测 + LLM 归因** | 混合策略 | 检测要可靠（规则），归因要智能（LLM） |
| **数据完整性环** | data_check 补调 + 熔断 | 代码层保证规则所需数据源不遗漏 |
| **数据摘要层** | summarize_for_analysis | 467 行 → 30 行，analyze/detect token 降 85% |
| **Gradio 而非 Streamlit** | gr.Blocks + async 原生 | Agent 全链路 async，零胶水代码 |
| **SqliteSaver 自实现** | 标准库 sqlite3 | 无需额外依赖，对话历史重启不丢 |
| **DeepSeek V4 Flash** | langchain-openai ChatOpenAI | 性价比最优，OpenAI 兼容可随时切换 |

---

## 评测结果

21 道测试题覆盖 5 种意图 × 8 个 MCP 工具，纯代码自动打分。

| 指标 | 结果 |
|------|------|
| 总分 | **23.5/24.5 (96%)** |
| 意图分类 | 19/20 (95%) |
| 工具选择 | 19/20 (95%) |
| 异常命中 | 6/6 规则全触发，全扫描最高 65 条异常 |
| 数据完整性环 | 触发补调 7 次，熔断降级 3 次 |

详见 [EVAL_REPORT.md](EVAL_REPORT.md)

```bash
# 运行评测
MCP_TRANSPORT=streamable-http MCP_PORT=8000 python -m src.mcp_server.server  # 终端 1
python tests/test_eval.py                                                      # 终端 2
```

---

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 MCP | 8 个语义化数据工具 + store_id 过滤 + streamable-http | ✅ |
| Phase 1 Agent | 8 节点全框架 + 数据完整性环 + 6 条规则扫描 + Function Calling | ✅ |
| Phase 1 评测 | 10 题 92% | ✅ |
| Phase 2 评测 | 扩到 20 题 + prompt 调优 → 96% | ✅ |
| Phase 2 UI | Gradio Web 聊天界面 + 进度树 + 流式输出 | ✅ |
| Phase 2 持久化 | SqliteSaver + 多轮对话 + 追问代词解析 | ✅ |
| Phase 2 优化 | 数据摘要层 + clarify 反问 + start.sh | ✅ |
| Phase 3 | 计划中 | — |
