"""
LangGraph 状态机 — 7 节点 + 4 条件路由。

全链路: understand → plan → call_tools → analyze → detect → suggest → respond
短路:  chat → respond;  lookup → respond (skip detect/suggest)
"""
import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .mcp_client import get_client
from .logger import log_node_end, log_node_start
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
# DEEPSEEK_MODEL = "deepseek-v4-pro"
DEEPSEEK_MODEL = "deepseek-v4-flash"

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
    log_node_start("understand", state)
    system = UNDERSTAND_SYSTEM.format(user_query=state["user_query"])
    response = await simple_llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=state["user_query"]),
    ])
    try:
        result = json.loads(response.content)
        output = {
            "intent": result.get("intent", "lookup"),
            "entities": result.get("entities", {}),
        }
    except json.JSONDecodeError:
        output = {"intent": "chat", "entities": {}, "error": "Intent parsing failed"}
    log_node_end("understand", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ② plan — LLM + Function Calling 决定工具调用
# ═══════════════════════════════════════════════════════════════════

async def plan_node(state: AgentState, plan_llm) -> dict:
    """plan_llm 通过闭包注入（build_graph 时 bind_tools 后传入）。
    补调轮次：missing_sources 非空时追加提示，要求 LLM 补调缺失工具。"""
    log_node_start("plan", state)
    missing = state.get("missing_sources", [])
    iteration = state.get("plan_iteration") or 0

    system = PLAN_SYSTEM.format(
        intent=state.get("intent", "lookup"),
        entities=state.get("entities", {}),
        today=date.today().isoformat(),
    )
    if iteration > 0 and missing:
        system += (
            f"\n\n⚠️ 数据完整性检查未通过。以下数据源缺失：{missing}。"
            f"请调用对应的 MCP 工具补全数据（使用与之前相同的日期参数），"
            f"否则依赖这些数据源的异常规则将被跳过。"
            f"只补调缺失的工具即可，已获取的数据无需重复调用。"
        )

    messages = [SystemMessage(content=system)]
    for msg in state.get("messages", []):
        if isinstance(msg, (HumanMessage, AIMessage)):
            messages.append(msg)

    response = await plan_llm.ainvoke(messages)
    output = {"messages": [response]}
    log_node_end("plan", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ③ call_tools — MCP streamable-http 并行执行
# ═══════════════════════════════════════════════════════════════════

async def call_tools_node(state: AgentState) -> dict:
    log_node_start("call_tools", state)
    messages = state.get("messages", [])
    if not messages:
        output = {"tool_results": {}, "error": "No messages in state"}
        log_node_end("call_tools", output)
        return output

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        output = {"tool_results": {}, "error": "No tool calls found in plan response"}
        log_node_end("call_tools", output)
        return output

    tool_calls = last_msg.tool_calls
    calls = [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls]

    client = await get_client()
    results = await client.call_tools_parallel(calls)

    # 补调场景：合并已有 tool_results，保留前一轮的结果
    results = {**state.get("tool_results", {}), **results}

    tool_messages = []
    for i, tc in enumerate(tool_calls):
        name = tc["name"]
        # 先用序号 key 找，找不到用原名找
        key = f"{name}#{i}" if len(calls) > 1 else name
        result = results.get(key, results.get(name, {"data": [], "row_count": 0, "error": "Tool not executed"}))
        tool_messages.append(ToolMessage(
            content=json.dumps(result, ensure_ascii=False, default=str),
            tool_call_id=tc["id"],
            name=name,
        ))

    output = {"messages": tool_messages, "tool_results": results}
    log_node_end("call_tools", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ③½ data_check — 数据完整性校验（纯代码，无 LLM 调用）
# ═══════════════════════════════════════════════════════════════════

def _get_required_sources(config: dict) -> dict[str, list[str]]:
    """从 anomaly_rules 提取每个 data_source 被哪些规则依赖。
    返回 {"get_ad_performance": ["广告DRR过高", "高点击低转化"], ...}
    """
    source_to_rules: dict[str, list[str]] = {}
    for rule_name, rule in config.get("anomaly_rules", {}).items():
        for src in rule.get("data_source", "").split("+"):
            src = src.strip()
            if src:
                source_to_rules.setdefault(src, []).append(rule_name)
    return source_to_rules


def data_check_node(state: AgentState) -> dict:
    """检查 tool_results 是否覆盖了 anomaly_rules 所需的全部数据源。
    非异常意图直接通过，无 LLM 成本。"""
    log_node_start("data_check", state)
    logger = logging.getLogger("ozon-agent")
    intent = state.get("intent", "")
    tool_results = state.get("tool_results", {})
    iteration = state.get("plan_iteration") or 0
    iteration += 1

    # 非异常意图不需要完整性检查
    if intent not in ("anomaly", "advice"):
        output = {"plan_iteration": iteration, "missing_sources": [], "skipped_rules": []}
        log_node_end("data_check", output)
        return output

    source_to_rules = _get_required_sources(state.get("config", {}))
    if not source_to_rules:
        output = {"plan_iteration": iteration, "missing_sources": [], "skipped_rules": []}
        log_node_end("data_check", output)
        return output

    missing_sources = []
    skipped_rules = []
    for src, rules in source_to_rules.items():
        found = any(key.startswith(src) for key in tool_results)
        if not found:
            missing_sources.append(src)
            skipped_rules.extend(rules)

    if missing_sources:
        if iteration >= 2:
            logger.warning("  data_check: CIRCUIT BREAKER — %d iterations, missing=%s, skipped_rules=%s",
                           iteration, missing_sources, skipped_rules)
        else:
            logger.info("  data_check: missing=%s → re-plan", missing_sources)
    else:
        logger.debug("  data_check: all %d sources covered", len(source_to_rules))

    output = {
        "plan_iteration": iteration,
        "missing_sources": missing_sources,
        "skipped_rules": skipped_rules,
    }
    log_node_end("data_check", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ④ analyze — 数据解读 + 交叉验证
# ═══════════════════════════════════════════════════════════════════

async def analyze_node(state: AgentState) -> dict:
    log_node_start("analyze", state)
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
    output = {"analysis": response.content}
    log_node_end("analyze", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ⑤ detect — 规则扫描（代码）+ 归因（LLM）
# ═══════════════════════════════════════════════════════════════════

def _resolve_severity(fields: dict, severity_map: dict, ref_field: str | None) -> str:
    """从 severity_map 解析严重级别。按定义顺序，首个匹配的级别胜出。
    例：{"critical": "< 0 → 亏损", "warning": "< 10 → 利润过低"}"""
    import re

    if not severity_map or not ref_field:
        return "warning"

    field_val = fields.get(ref_field)
    if field_val is None:
        return "warning"

    OP_MAP = {
        "<":  lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">":  lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
    }

    for level, desc in severity_map.items():
        m = re.search(r'([<>]=?)\s*(-?\d+\.?\d*)', desc)
        if not m:
            continue
        op_str, val_str = m.group(1), m.group(2)
        try:
            target = float(val_str)
        except ValueError:
            continue
        if OP_MAP.get(op_str, lambda a, b: False)(field_val, target):
            return level

    return "warning"


def _check_threshold(tool_results: dict, rule_config: dict, rule_name: str) -> list:
    """阈值检测：遍历 conditions，对 tool_results 中对应数据源的每行做字段比对。

    支持多数据源按 sku_id 合并、gte/lte/lt/gt/eq 操作符、
    require: all 逻辑、severity_map 分级。
    """
    # 1. 解析数据源名称  "get_returns + get_daily_summary" → ["get_returns", "get_daily_summary"]
    data_source_str = rule_config.get("data_source", "")
    source_names = [s.strip() for s in data_source_str.split("+")]

    # 2. 从 tool_results 收集匹配数据，按 sku_id 合并行
    merged: dict[int, dict] = {}  # sku_id → {合并字段}
    for source_name in source_names:
        for key, result in tool_results.items():
            if not key.startswith(source_name):
                continue
            if result.get("error"):
                continue
            for row in result.get("data", []):
                sku_id = row.get("sku_id") or row.get("sku")  # get_returns 表列名是 sku
                if sku_id is None:
                    continue
                merged.setdefault(sku_id, {}).update(row)

    if not merged:
        return []

    # 3. 提取规则配置
    conditions = rule_config.get("conditions", [])
    require_all = rule_config.get("require", "all") == "all"
    severity_map = rule_config.get("severity_map", {})
    ref_field = conditions[0]["field"] if conditions else None

    OPS = {
        "gte": lambda a, b: a >= b,
        "lte": lambda a, b: a <= b,
        "lt":  lambda a, b: a < b,
        "gt":  lambda a, b: a > b,
        "eq":  lambda a, b: a == b,
    }

    # 4. 逐行评估条件
    anomalies = []
    missing_count: dict[str, int] = {}  # field → 缺失行数
    required_fields = {c["field"] for c in conditions}
    for sku_id, fields in merged.items():
        hits = []
        for cond in conditions:
            field_name = cond["field"]
            op = cond["op"]
            target = cond["value"]
            actual = fields.get(field_name)
            if actual is None:
                hits.append(False)
                continue
            try:
                hits.append(OPS.get(op, lambda a, b: False)(actual, target))
            except (TypeError, ValueError):
                hits.append(False)

        if require_all and not all(hits):
            for f in required_fields - set(fields.keys()):
                missing_count[f] = missing_count.get(f, 0) + 1
            continue

        severity = _resolve_severity(fields, severity_map, ref_field)
        anomalies.append({
            "type": rule_name,
            "severity": severity,
            "sku_id": sku_id,
            "detail": {c["field"]: fields.get(c["field"]) for c in conditions},
            "description": rule_config.get("description", ""),
        })

    # 诊断：数据源在 tool_results 中未找到（放在 merged 判空前，确保空数据也能报）
    _log = logging.getLogger("ozon-agent")
    found_sources = set()
    for sn in source_names:
        for key in tool_results:
            if key.startswith(sn):
                found_sources.add(sn)
                break
    for sn in source_names:
        if sn not in found_sources:
            _log.warning("  detect/%s: data_source %r not found in tool_results (keys=%s)",
                         rule_name, sn, list(tool_results.keys()))

    if not merged:
        return []

    # 诊断：字段缺失统计（按缺失行数降序）
    for f, cnt in sorted(missing_count.items(), key=lambda x: -x[1]):
        _log.warning("  detect/%s: field %r missing in %d/%d rows",
                     rule_name, f, cnt, len(merged))

    return anomalies


def _check_mom(tool_results: dict, rule_config: dict, rule_name: str) -> list:
    """环比检测：对比当前窗口 vs 前 N 天均值。
    框架占位——需要前后两段时间窗口的数据做对比。"""
    return []


RULE_HANDLERS = {
    "环比": _check_mom,
    "阈值": _check_threshold,
}


async def detect_node(state: AgentState) -> dict:
    log_node_start("detect", state)
    logger = logging.getLogger("ozon-agent")
    anomalies = []
    config = state.get("config", {})
    rules = config.get("anomaly_rules", {})
    tool_results = state.get("tool_results", {})

    logger.debug("  detect: %d rules, tool_results keys=%s",
                 len(rules), list(tool_results.keys()))

    # ── 规则扫描（代码层）──
    for rule_name, rule_config in rules.items():
        rule_type = rule_config.get("type", "")
        handler = RULE_HANDLERS.get(rule_type)
        if not handler:
            logger.warning("  detect/%s: no handler for type=%r, skipped", rule_name, rule_type)
            continue
        data_source = rule_config.get("data_source", "?")
        logger.debug("  detect/%s: type=%s, source=%s", rule_name, rule_type, data_source)
        result = handler(tool_results, rule_config, rule_name)
        if result:
            logger.info("  detect/%s: ⚠ %d anomalies", rule_name, len(result))
        else:
            logger.debug("  detect/%s: 0 anomalies (no match)", rule_name)
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

    output = {"anomalies": anomalies}
    log_node_end("detect", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ⑥ suggest — 运营建议
# ═══════════════════════════════════════════════════════════════════

async def suggest_node(state: AgentState) -> dict:
    log_node_start("suggest", state)
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

    output = {"suggestions": suggestions}
    log_node_end("suggest", output)
    return output


# ═══════════════════════════════════════════════════════════════════
# ⑦ respond — 组装最终回答
# ═══════════════════════════════════════════════════════════════════

async def respond_node(state: AgentState) -> dict:
    log_node_start("respond", state)
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
    output = {"final_answer": response.content}
    log_node_end("respond", output)
    return output


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


def route_after_data_check(state: AgentState) -> str:
    """数据完整性检查后路由：
    - 非 anomaly/advice → 直接 analyze
    - 数据完整 → analyze
    - 缺数据 + 未超限 → 回 plan 补调
    - 缺数据 + 已超限(≥2轮) → 降级进 analyze
    """
    intent = state.get("intent", "")
    if intent not in ("anomaly", "advice"):
        return "analyze"

    missing = state.get("missing_sources", [])
    iteration = state.get("plan_iteration") or 0

    if not missing:
        return "analyze"

    if iteration >= 2:
        logger = logging.getLogger("ozon-agent")
        logger.warning("  route_after_data_check: circuit breaker — proceeding to analyze")
        return "analyze"

    return "plan"


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
    builder.add_node("data_check", data_check_node)
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
    # 数据完整性环：call_tools → data_check → analyze | plan
    builder.add_edge("call_tools", "data_check")
    builder.add_conditional_edges("data_check", route_after_data_check, {
        "analyze": "analyze",
        "plan": "plan",
    })
    builder.add_conditional_edges("analyze", route_after_analyze, {
        "respond": "respond",
        "detect": "detect",
    })
    builder.add_edge("detect", "suggest")
    builder.add_edge("suggest", "respond")
    builder.add_edge("respond", END)

    return builder.compile(checkpointer=MemorySaver())
