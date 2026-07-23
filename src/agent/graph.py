"""
LangGraph 状态机 — 7 节点 + 4 条件路由。

全链路: understand → plan → call_tools → analyze → detect → suggest → respond
短路:  chat → respond;  lookup → respond (skip detect/suggest)
"""
import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .mcp_client import get_client
from .prompts import (
    ANALYZE_SYSTEM,
    DETECT_ATTRIBUTION_PROMPT,
    PLAN_SYSTEM,
    RESPOND_SYSTEM,
    SUGGEST_SYSTEM,
    UNDERSTAND_SYSTEM,
)
from .state import AgentState
from .tool_adapter import adapt_tools

# ═══════════════════════════════════════════════════════════════════
# LLM 初始化
# ═══════════════════════════════════════════════════════════════════

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

_api_key = os.getenv("DEEPSEEK_API_KEY", "")
if _api_key:
    _api_key = _api_key.strip('"').strip("'")

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"

simple_llm = ChatOpenAI(
    model=DEEPSEEK_MODEL,
    base_url=DEEPSEEK_BASE,
    api_key=_api_key,
    temperature=0.1,
)

full_llm = ChatOpenAI(
    model=DEEPSEEK_MODEL,
    base_url=DEEPSEEK_BASE,
    api_key=_api_key,
    temperature=0.3,
)


# ═══════════════════════════════════════════════════════════════════
# ① understand — 意图分类 + 实体提取
# ═══════════════════════════════════════════════════════════════════

async def understand_node(state: AgentState) -> dict:
    system = UNDERSTAND_SYSTEM.format(user_query=state["user_query"])
    response = await simple_llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=state["user_query"]),
    ])
    try:
        result = json.loads(response.content)
        return {
            "intent": result.get("intent", "lookup"),
            "entities": result.get("entities", {}),
        }
    except json.JSONDecodeError:
        return {"intent": "chat", "entities": {}, "error": "Intent parsing failed"}


# ═══════════════════════════════════════════════════════════════════
# ② plan — LLM + Function Calling 决定工具调用
# ═══════════════════════════════════════════════════════════════════

async def plan_node(state: AgentState, plan_llm) -> dict:
    """plan_llm 通过闭包注入（build_graph 时 bind_tools 后传入）。"""
    system = PLAN_SYSTEM.format(
        intent=state.get("intent", "lookup"),
        entities=state.get("entities", {}),
        today=date.today().isoformat(),
    )
    messages = [SystemMessage(content=system)]
    for msg in state.get("messages", []):
        if isinstance(msg, (HumanMessage, AIMessage)):
            messages.append(msg)

    response = await plan_llm.ainvoke(messages)
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════════
# ③ call_tools — MCP streamable-http 并行执行
# ═══════════════════════════════════════════════════════════════════

async def call_tools_node(state: AgentState) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {"tool_results": {}, "error": "No messages in state"}

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {"tool_results": {}, "error": "No tool calls found in plan response"}

    tool_calls = last_msg.tool_calls
    calls = [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls]

    client = await get_client()
    results = await client.call_tools_parallel(calls)

    tool_messages = []
    for tc in tool_calls:
        name = tc["name"]
        result = results.get(name, {"data": [], "row_count": 0, "error": "Tool not executed"})
        tool_messages.append(ToolMessage(
            content=json.dumps(result, ensure_ascii=False, default=str),
            tool_call_id=tc["id"],
            name=name,
        ))

    return {"messages": tool_messages, "tool_results": results}


# ═══════════════════════════════════════════════════════════════════
# ④ analyze — 数据解读 + 交叉验证
# ═══════════════════════════════════════════════════════════════════

async def analyze_node(state: AgentState) -> dict:
    config_metrics = state.get("config", {}).get("metrics", {})
    tool_results = state.get("tool_results", {})

    system = ANALYZE_SYSTEM.format(
        config_metrics=json.dumps(config_metrics, ensure_ascii=False),
        tool_results=json.dumps(tool_results, ensure_ascii=False, default=str),
    )
    response = await full_llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content="请分析以上数据。"),
    ])
    return {"analysis": response.content}


# ═══════════════════════════════════════════════════════════════════
# ⑤ detect — 规则扫描（代码）+ 归因（LLM）
# ═══════════════════════════════════════════════════════════════════

def _check_threshold(tool_results: dict, rule_config: dict, rule_name: str) -> list:
    """阈值检测：检查指标是否超过 threshold。
    框架占位——具体检测逻辑基于实际数据结构适配。"""
    return []


def _check_mom(tool_results: dict, rule_config: dict, rule_name: str) -> list:
    """环比检测：对比当前窗口 vs 前 N 天均值。
    框架占位——需要前后两段时间窗口的数据做对比。"""
    return []


RULE_HANDLERS = {
    "环比": _check_mom,
    "阈值": _check_threshold,
}


async def detect_node(state: AgentState) -> dict:
    anomalies = []
    config = state.get("config", {})
    rules = config.get("anomaly_rules", {})
    tool_results = state.get("tool_results", {})

    # ── 规则扫描（代码层）──
    for rule_name, rule_config in rules.items():
        rule_type = rule_config.get("type", "")
        handler = RULE_HANDLERS.get(rule_type)
        if handler:
            result = handler(tool_results, rule_config, rule_name)
            anomalies.extend(result)

    # ── LLM 归因 ──
    if anomalies:
        prompt = DETECT_ATTRIBUTION_PROMPT.format(
            anomalies_marked=json.dumps(anomalies, ensure_ascii=False),
            tool_results=json.dumps(tool_results, ensure_ascii=False, default=str),
        )
        response = await full_llm.ainvoke([HumanMessage(content=prompt)])
        try:
            attributed = json.loads(response.content)
            if isinstance(attributed, list):
                anomalies = attributed
        except json.JSONDecodeError:
            pass

    return {"anomalies": anomalies}


# ═══════════════════════════════════════════════════════════════════
# ⑥ suggest — 运营建议
# ═══════════════════════════════════════════════════════════════════

async def suggest_node(state: AgentState) -> dict:
    config_metrics = state.get("config", {}).get("metrics", {})
    system = SUGGEST_SYSTEM.format(
        analysis=state.get("analysis", ""),
        anomalies=json.dumps(state.get("anomalies", []), ensure_ascii=False),
        config_metrics=json.dumps(config_metrics, ensure_ascii=False),
    )
    response = await full_llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content="请基于以上信息生成运营建议。"),
    ])

    suggestions = []
    for line in response.content.strip().split("\n"):
        line = line.strip()
        if line and (line[0].isdigit() or line.startswith("- ") or line.startswith("🔴") or line.startswith("🟡") or line.startswith("🟢")):
            suggestions.append(line)

    return {"suggestions": suggestions}


# ═══════════════════════════════════════════════════════════════════
# ⑦ respond — 组装最终回答
# ═══════════════════════════════════════════════════════════════════

async def respond_node(state: AgentState) -> dict:
    context = {
        "user_query": state.get("user_query", ""),
        "intent": state.get("intent", ""),
        "analysis": state.get("analysis", ""),
        "anomalies": state.get("anomalies", []),
        "suggestions": state.get("suggestions", []),
        "tool_results_summary": {
            name: {"row_count": r.get("row_count", 0), "error": r.get("error")}
            for name, r in state.get("tool_results", {}).items()
        },
    }

    system = RESPOND_SYSTEM.format(
        context=json.dumps(context, ensure_ascii=False, default=str)
    )

    messages = [SystemMessage(content=system)]
    if state.get("intent") == "chat":
        messages.append(HumanMessage(content=state["user_query"]))
    else:
        messages.append(HumanMessage(content="请生成最终回答。"))

    response = await simple_llm.ainvoke(messages)
    return {"final_answer": response.content}


# ═══════════════════════════════════════════════════════════════════
# 路由函数
# ═══════════════════════════════════════════════════════════════════

def route_after_understand(state: AgentState) -> str:
    if state.get("intent") == "chat":
        return "respond"
    return "plan"


def route_after_plan(state: AgentState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return "respond"
    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "call_tools"
    return "respond"


def route_after_analyze(state: AgentState) -> str:
    intent = state.get("intent", "lookup")
    if intent in ("anomaly", "advice"):
        return "detect"
    return "respond"


# ═══════════════════════════════════════════════════════════════════
# build_graph — 异步构建（先连 MCP Server 拿工具列表）
# ═══════════════════════════════════════════════════════════════════

async def build_graph() -> StateGraph:
    # 1. 连接 MCP Server，获取工具列表 → 转 OpenAI function 格式
    client = await get_client()
    mcp_tools = client.tools
    tool_defs = adapt_tools(mcp_tools)

    # 2. 创建带 Function Calling 的 plan LLM
    plan_llm = full_llm.bind_tools(tool_defs)

    # plan_node 闭包包装（必须 async def，lambda 返回 coroutine 会导致 InvalidUpdateError）
    async def _plan_node(state: AgentState) -> dict:
        return await plan_node(state, plan_llm)

    # 3. 构建状态机
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("understand", understand_node)
    builder.add_node("plan", _plan_node)
    builder.add_node("call_tools", call_tools_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("detect", detect_node)
    builder.add_node("suggest", suggest_node)
    builder.add_node("respond", respond_node)

    # 4. 边 + 条件路由
    builder.add_edge(START, "understand")
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
