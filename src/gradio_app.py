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

# 节点 → 阶段标题（只对顶层图节点显示）
NODE_SECTION = {
    "understand": "🔍 意图识别",
    "plan":       "📋 查询规划",
    "call_tools": "📊 数据获取",
    "data_check": "✅ 完整性检查",
    "analyze":    "🧠 数据分析",
    "detect":     "⚠️ 异常检测",
    "suggest":    "💡 运营建议",
    "respond":    "📝 最终回答",
}


async def respond(message: str, history: list):
    """流式执行 Agent，展示 LLM 逐 token 思考过程 + 节点进度。"""
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

    sections: list[tuple[str, list[str]]] = []  # [(标题, [内容...]), ...]
    seen: set[str] = set()                       # 已出现过的标题
    current_llm_node: str | None = None          # 当前在生成 token 的节点
    final_answer = ""

    async for event in graph.astream_events(state, config, version="v2"):
        kind = event["event"]
        name = event["name"]

        # ── 节点开始 → 插入阶段标题 ──
        if kind == "on_chain_start" and name in NODE_SECTION:
            title = NODE_SECTION[name]
            if title not in seen:
                seen.add(title)
                sections.append((title, []))
            if name in ("understand", "plan", "analyze", "detect", "suggest", "respond"):
                current_llm_node = name

        # ── LLM 逐 token 输出 ──
        elif kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                token = chunk.content
                if isinstance(token, list):
                    token = "".join(str(t) for t in token if isinstance(t, str))
                if token and current_llm_node:
                    title = NODE_SECTION.get(current_llm_node, current_llm_node)
                    for sec_title, tokens in sections:
                        if sec_title == title:
                            tokens.append(token)
                            break

        # ── 节点结束 → 捕获 tool_results 和 final_answer ──
        elif kind == "on_chain_end" and name in NODE_SECTION:
            if name == current_llm_node:
                current_llm_node = None  # LLM 阶段结束

            output = event["data"].get("output", {})

            if name == "call_tools" and isinstance(output, dict):
                results = output.get("tool_results", {})
                title = NODE_SECTION["call_tools"]
                for sec_title, tokens in sections:
                    if sec_title == title:
                        for k, v in results.items():
                            tool_name = k.split("#")[0]
                            rows = v.get("row_count", 0)
                            err = v.get("error")
                            if err:
                                tokens.append(f"\n❌ {tool_name}: {err}")
                            else:
                                tokens.append(f"\n✅ {tool_name}: {rows} 行")
                        break

            if name == "respond" and isinstance(output, dict):
                final_answer = output.get("final_answer", "")

        # ── 组装当前显示 ──
        display = ""
        for sec_title, tokens in sections:
            display += f"\n### {sec_title}\n"
            display += "".join(tokens)
        if display.strip():
            yield display

    # 最终输出
    if final_answer:
        yield final_answer
    elif sections:
        display = ""
        for sec_title, tokens in sections:
            display += f"\n### {sec_title}\n"
            display += "".join(tokens)
        yield display
    else:
        yield "⚠️ 未生成回答，请重试。"


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
