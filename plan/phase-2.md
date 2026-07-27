# Phase 2：评测闭环 + 展示 + 交付

> 目标：把 Phase 1 的 CLI 原型升级为可演示的完整产品。
> 当前状态：MCP Server（8 工具）+ Agent（8 节点）已跑通，10 题评测 92%。
> 时间：第 3–4 周。

---

## 当前进度盘点

### 已完成 ✓

| 模块 | 实际产出 | 原计划 | 状态 |
|---|---|---|---|
| MCP Server | 8 个工具（products / postings / returns / finance / stock / ad_performance / ad_campaign_stats / daily_summary） | 4 个 | 超额完成 |
| LangGraph Agent | 8 节点 + 数据完整性环 + 熔断 | 7 节点 | 超额完成 |
| 配置驱动 | metrics.yaml（7 口径 + 6 异常规则） | 已设计 | 已完成 |
| 评测集 | 10 题，准确率 92% | 20 题 | 过半 |
| CLI 交互 | run.py 命令行对话 | CLI | 已完成 |
| 多轮对话 | MemorySaver checkpointer，thread_id 持久化 | — | 基础可用 |

### 已知问题

| 问题 | 影响 | 优先级 |
|---|---|---|
| eval 仅 10 题，且 #4/#9 工具匹配有偏差 | 评测覆盖率不足 | P0 |
| 无图形界面——CLI 不可演示 | 面试展示弱 | P0 |
| get_daily_summary 派生数据有极端值 | 分析准确性 | P1 |
| 多轮追问继承上下文不稳定 | 体验 | P1 |
| 无 README，第三方跑不起来 | 交付 | P0 |

---

## 关键决策：BI 仪表盘不在此项目内

BI 仪表盘已在另一个独立项目中实现，Phase 2 的展示层定位为**纯聊天界面**：

```
用户输入文字 → Agent 全链路 → LLM 输出 Markdown → 直接展示
```

**不需要**：指标卡片、Plotly 图表、右侧数据面板、趋势图、数据表格组件。

**展示层只做一件事**：Markdown 渲染的 Chat 界面。Agent 的 `final_answer` 本身就是结构化文本（表格、列表、emoji），不需要二次加工。

技术选型：**Gradio**，理由：
- `async def` 原生支持，Agent 全链路直接 `await`，零胶水代码
- `gr.ChatInterface(type="messages")` 一行搭出多轮对话
- Markdown 自动渲染（表格、代码块、emoji 全支持，对应 Agent 输出）
- 代码量 ~30 行 vs Streamlit ~80 行 + async hack

---

## 1. 评测闭环完善（P0）

### 1.1 扩评测集：10 → 20 题

当前 10 题覆盖：chat(1) / lookup(4) / anomaly(4) / advice(1)

需要补充的 10 题方向：

| # | 场景 | 意图 | 考察点 |
|---|---|---|---|
| 11 | "sku-123456 最近两周的广告投产比趋势" | lookup | 交叉分析 sales+ad |
| 12 | "对比两个店铺的净利润，哪个更赚钱？" | lookup | 店铺对比 + store_id 传参 |
| 13 | "哪些 SKU 退货率高但还在投广告？" | anomaly | 跨数据源关联 + 复合异常 |
| 14 | "广告花费 top5 的 SKU 转化率分别是多少？" | lookup | 排序 + 多指标 |
| 15 | "库存少于 5 件的 SKU 最近一周有没有订单？" | anomaly | 跨 stock + postings 关联 |
| 16 | "财务流水里有没有大额退款？" | anomaly | finance 工具 + 异常检测 |
| 17 | "针对广告花费高但不出单的 SKU 怎么优化？" | advice | 建议可执行性 |
| 18 | "这两个月退货率趋势怎么样？和上个月比呢？" | lookup | 时间对比 + 趋势分析 |
| 19 | "帮我看看整体经营状况，有什么要关注的？" | anomaly | 全扫描 + 综合诊断 |
| 20 | "SKU 毛利润排序，利润率最低的 5 个是谁？" | lookup | finance 工具 + 排序 |

评测集 schema（沿用现有格式）：

```json
{
  "id": 11,
  "label": "lookup 交叉分析",
  "intent": "lookup",
  "query": "sku-123456 最近两周的广告投产比趋势",
  "expected_intent": "lookup",
  "expected_tools": ["get_ad_performance", "get_daily_summary"],
  "expect_anomalies": false,
  "expect_suggestions": false
}
```

### 1.2 评测脚本

```python
# tests/run_eval.py
# 遍历 eval_questions.json → 逐题跑 Agent → 对比预期 → 输出报告

关键指标：
- 意图识别准确率
- 工具选择匹配率
- 异常规则命中数（anomaly/advice 意图）
- 建议是否非空（advice 意图）
```

### 1.3 评测目标

| 指标 | 当前 | 目标 |
|---|---|---|
| 总体评分 | 92%（10 题） | ≥ 90%（20 题） |
| 意图识别 | 10/10 | 20/20 |
| 工具选择 | 8/10（#4, #9 有偏差） | 18/20 |
| 异常检测覆盖率 | anomaly 题均触发 3-5 规则 | 保持 |
| 建议非空率 | advice 题 0/1（#9 未出建议） | 3/3 |

---

## 2. Gradio 聊天界面（P0）

### 2.1 为什么是 Gradio

| | Gradio | Streamlit |
|---|---|---|
| async Agent 接入 | ✅ 原生 `async def` | ❌ 需要后台线程 hack |
| 多轮 Chat | ✅ `ChatInterface(type="messages")` | 🟡 手动搭 |
| Markdown 渲染 | ✅ 自动（表格、代码、emoji） | ✅ 自动 |
| 代码量 | ~30 行 | ~80 行 |

Agent 全链路是 `async`，MCPClient 连接绑定在事件循环上。Gradio 的事件循环就是 async，`await graph.ainvoke()` 直接往里放——没有事件循环问题，不需要后台线程。

### 2.2 单页面结构

```
┌──────────────────────────────────────────────────┐
│  OZON 数据分析 Agent                              │
│  基于真实 OZON 数据 · 2 店铺 · ~100 商品            │
├──────────────────────────────────────────────────┤
│                                                   │
│  ┌─────────────────────────────────────┐         │
│  │  🤖 Agent                            │         │
│  │                                      │         │
│  │  最近 30 天整体经营状况分析...          │         │
│  │                                      │         │
│  │  ## 核心指标                          │         │
│  │  | 指标 | 数值 | 变化                 │         │
│  │  | ...  | ...  | ...                │         │
│  │                                      │         │
│  │  ## 异常告警                          │         │
│  │  🔴 SKU-8821 利润率异常 ...           │         │
│  │                                      │         │
│  │  ## 运营建议                          │         │
│  │  1. ...                              │         │
│  └─────────────────────────────────────┘         │
│                                                   │
│  ┌─────────────────────────────────────┐         │
│  │  🙋 用户                              │         │
│  │  最近30天有没有什么异常？               │         │
│  └─────────────────────────────────────┘         │
│                                                   │
│  ┌──────────────────────────────────────────────┐ │
│  │  🔍 输入问题...              [发送]           │ │
│  └──────────────────────────────────────────────┘ │
│                                                   │
│  快捷：[7天概览] [检查异常] [库存预警] [广告ROI]    │
└──────────────────────────────────────────────────┘
```

纯聊天，Markdown 渲染。Agent 输出的表格、emoji、列表全由 Gradio 自动处理。

### 2.3 代码骨架

```python
# src/gradio_app.py

import gradio as gr
from src.agent.graph import build_graph
from src.agent.config_loader import load_config
from langchain_core.messages import HumanMessage

graph = None

async def respond(message: str, history: list):
    """每次用户发消息时调用，直接 await Agent 全链路。"""
    state = {
        "user_query": message,
        "messages": [HumanMessage(content=message)],
        "config": load_config(),
    }
    result = await graph.ainvoke(
        state,
        {"configurable": {"thread_id": "gradio-session"}},
    )
    return result.get("final_answer", "(无输出)")

async def main():
    global graph
    print("正在连接 MCP Server...")
    graph = await build_graph()
    print("✅ Agent 就绪。")

    demo = gr.ChatInterface(
        respond,
        type="messages",                # 多轮对话，history 自动管理
        title="OZON 数据分析 Agent",
        description="基于真实 OZON 数据 · 2 店铺 · ~100 商品",
        examples=[
            "最近7天整体经营状况怎么样？",
            "有没有退货异常的SKU？",
            "库存告急的SKU有哪些？",
            "广告投放ROI怎么样？有浪费的吗？",
        ],
    )
    demo.launch(server_port=8501)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### 2.4 依赖新增

```txt
gradio>=5.0.0
```

不再需要 `streamlit` 和 `plotly`。

---

## 3. README 交付文档（P0）

### 3.1 结构

```markdown
# OZON 数据分析 Agent

[一句话定位 + 架构图]

## 快速开始
- 环境要求
- 安装依赖
- 配置 .env
- 启动 MCP Server
- 启动 Agent CLI / Gradio UI

## 功能演示
- 截图 + 示例对话

## 技术架构
- Harness 三层映射
- MCP 工具列表
- LangGraph 状态机流程

## 评测
- 准确率
- 怎么跑评测

## 项目结构
- 目录树

## 关键决策
- 为什么不做 Text-to-SQL
- 为什么不用 RAG
- 为什么规则检测 + LLM 归因
- 为什么 Gradio 而不是 Streamlit
```

---

## 4. 多轮对话打磨（P1）

### 4.1 当前问题

- checkpointer 用了 MemorySaver，重启丢失
- 追问时上下文继承不稳定（"那个 SKU 呢？" 有时找不回上一轮的 SKU）
- chat intent 的闲聊回复过于机械

### 4.2 改进项

| 改进 | 方式 |
|---|---|
| 持久化 checkpointer | 换 SqliteSaver（`langgraph.checkpoint.sqlite`），对话历史不丢 |
| 追问实体补全 | respond prompt 中强调 "如果用户用代词指代，从对话历史中解析" |
| 澄清反问 | 实体提取不完整时 understand 返回 intent="clarify"，引导用户补充 |

---

## 5. 部署演示（P0）

### 5.1 本地一键启动脚本

```bash
# start.sh (Git Bash)
source venv/Scripts/activate

# 启动 MCP Server（后台）
MCP_TRANSPORT=streamable-http MCP_PORT=8000 python -m src.mcp_server.server &
MCP_PID=$!
echo "MCP Server PID: $MCP_PID"

sleep 2

# 启动 Gradio
python src/gradio_app.py

kill $MCP_PID
```

### 5.2 可选：Docker 一键部署

```dockerfile
# 简单 Dockerfile，面试加分（展示你懂容器化）
# 不强制，如果时间不够可跳过
```

---

## 6. 开发顺序

### 第 3 周：评测 + Gradio 骨架

- [ ] 评测集扩到 20 题，每道题写好 `expected_*` 字段
- [ ] 跑评测 → 记录新 10 题得分 → 对比 Phase 1 的 92%
- [ ] 针对 #4, #9 等已知问题调 prompt 或 tool description
- [ ] `pip install gradio`
- [ ] `src/gradio_app.py`：ChatInterface + 快捷示例

### 第 4 周：完善 + 交付

- [ ] 针对 get_daily_summary 极端值问题优化 prompt 提醒
- [ ] 多轮对话：SqliteSaver 持久化 + 追问代词解析
- [ ] README.md 完整文档
- [ ] 启动脚本 start.sh
- [ ] 全程用 Gradio 录一段演示（面试用）

---

## 7. Phase 2 验收标准

- [ ] 20 题评测跑通，总体评分 ≥ 90%
- [ ] Gradio 界面可演示：多轮对话 + Markdown 渲染 + 快捷问题
- [ ] README 完整，第三方能照着跑起来
- [ ] `start.sh` 一键启动
- [ ] 多轮对话：追问代词正确解析，上下文不丢

Phase 2 完成的标志：**打开 Gradio 页面 → 点快捷"检查异常" → 看到异常列表 + 归因分析 + 建议 → 追问"那个 SKU 的广告怎么优化？" → Agent 正确继承上下文并给出回答。**

---

## 8. 依赖更新

```bash
source venv/Scripts/activate
pip install gradio
pip freeze > requirements.txt
```
