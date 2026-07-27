"""
Gradio 聊天界面 — Agent 展示层。

启动方式：
  终端 1: MCP_TRANSPORT=streamable-http MCP_PORT=8000 python -m src.mcp_server.server
  终端 2: python -m src.gradio_app
"""
import asyncio

import gradio as gr
from langchain_core.messages import AIMessageChunk, HumanMessage

from src.agent.graph import build_graph
from src.agent.config_loader import load_config

graph = None
_init_lock = asyncio.Lock()

NODE_LABEL = {
    "understand": "分析意图",
    "plan":       "规划查询",
    "call_tools": "获取数据",
    "data_check": "完整性检查",
    "analyze":    "分析数据",
    "detect":     "检测异常",
    "suggest":    "生成建议",
    "respond":    "生成回答",
}

NODE_ORDER = ["understand", "plan", "call_tools", "data_check", "analyze", "detect", "suggest", "respond"]


def _build_progress(node_state: dict, tool_parts: list[str]) -> str:
    """根据节点状态拼进度树。"""
    lines = []
    for name in NODE_ORDER:
        status = node_state.get(name)
        if status is None:
            continue
        label = NODE_LABEL[name]
        if status == "active":
            lines.append(f"⏳ {label}...")
        elif status == "done":
            if name == "call_tools" and tool_parts:
                lines.append(f"✅ {label}")
                for tp in tool_parts:
                    lines.append(f"   {tp}")
            else:
                suffix = node_state.get("_anomaly_count", "") if name == "detect" else ""
                lines.append(f"✅ {label}{suffix}")
    return "\n".join(lines)


async def respond(message: str, history: list):
    global graph
    if graph is None:
        async with _init_lock:
            if graph is None:
                print("正在连接 MCP Server...")
                graph = await build_graph()
                print("✅ Agent 就绪。")

    state = {
        "user_query": message,
        "messages": [HumanMessage(content=message)],
        "config": load_config(),
    }
    config = {"configurable": {"thread_id": "gradio-session"}}

    node_state: dict[str, str] = {}
    tool_parts: list[str] = []
    answer_tokens: list[str] = []

    async for event in graph.astream_events(state, config, version="v2"):
        kind = event["event"]
        name = event["name"]

        # ── 节点开始 → 立即推送 ⏳ ──
        if kind == "on_chain_start" and name in NODE_LABEL:
            node_state[name] = "active"
            if name != "respond":
                yield _build_progress(node_state, tool_parts)

        # ── LLM 逐 token（只捕获 respond 节点）──
        elif kind == "on_chat_model_stream" and node_state.get("respond") == "active":
            chunk = event["data"]["chunk"]
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                token = chunk.content
                if isinstance(token, list):
                    token = "".join(str(t) for t in token if isinstance(t, str))
                if token:
                    answer_tokens.append(token)
                    progress = _build_progress(node_state, tool_parts)
                    yield progress + "\n\n---\n\n" + "".join(answer_tokens)

        # ── 节点结束 ──
        elif kind == "on_chain_end" and name in NODE_LABEL:
            if name == "call_tools":
                output = event["data"].get("output", {})
                results = output.get("tool_results", {})
                tool_parts.clear()
                for k, v in results.items():
                    tool_name = k.split("#")[0]
                    rows = v.get("row_count", 0)
                    err = v.get("error")
                    if err:
                        tool_parts.append(f"❌ {tool_name}: {err}")
                    else:
                        tool_parts.append(f"✅ {tool_name}: {rows} 行")

            elif name == "detect":
                output = event["data"].get("output", {})
                anomalies = output.get("anomalies", [])
                if anomalies:
                    critical = sum(1 for a in anomalies if a.get("severity") == "critical")
                    warning = sum(1 for a in anomalies if a.get("severity") == "warning")
                    parts = []
                    if critical:
                        parts.append(f"🔴 {critical}")
                    if warning:
                        parts.append(f"🟡 {warning}")
                    node_state["_anomaly_count"] = f" → 发现 {' '.join(parts)}"

            node_state[name] = "done"
            # respond 结束时跳过——流式阶段已在输出，最终 yield 会一起展示
            if name != "respond":
                yield _build_progress(node_state, tool_parts)

    # 最终输出：进度 + 回答一起保留
    answer = "".join(answer_tokens) if answer_tokens else ""
    if answer:
        yield _build_progress(node_state, tool_parts) + "\n\n---\n\n" + answer


if __name__ == "__main__":
    demo = gr.ChatInterface(
        respond,
        title="OZON 小帮手",
        description="基于真实 OZON 店铺后台的数据",
        examples=[
            "最近7天整体经营状况怎么样？",
            "有没有退货异常的SKU？",
            "库存告急的SKU有哪些？",
            "广告投放ROI怎么样？有浪费的吗？",
        ],
    )
    demo.launch(server_port=8501)
