"""
Gradio 聊天界面 — Agent 展示层。

启动方式：
  终端 1: MCP_TRANSPORT=streamable-http MCP_PORT=8000 python -m src.mcp_server.server
  终端 2: python -m src.gradio_app
"""
import asyncio
import uuid

import gradio as gr
from langchain_core.messages import HumanMessage

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


def _build_progress(node_state: dict) -> str:
    lines = []
    for name in NODE_ORDER:
        status = node_state.get(name)
        if status is None:
            continue
        label = NODE_LABEL[name]
        if status == "active":
            lines.append(f"⏳ {label}...")
        elif status == "done":
            lines.append(f"✅ {label}")

    return "\n".join(lines)

async def run_agent(message: str, thread_id: str):
    """执行 Agent，yield 进度字符串。"""
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
        "final_answer": "",   # 清掉上一轮 checkpoint 残留的旧值
    }
    config = {"configurable": {"thread_id": thread_id}}

    node_state: dict[str, str] = {}

    async for event in graph.astream_events(state, config, version="v2"):
        kind = event["event"]
        name = event["name"]
        # ── 节点开始 ──
        if kind == "on_chain_start" and name in NODE_LABEL:
            node_state[name] = "active"
            yield _build_progress(node_state)
        # ── 节点结束 ──
        elif kind == "on_chain_end" and name in NODE_LABEL and name != "respond":
            node_state[name] = "done"
            yield _build_progress(node_state)
        elif kind == "on_chain_end" and name == "respond":
            node_state[name] = "done"
            output = event["data"].get("output", {})
            final = output.get("final_answer", "")
            yield _build_progress(node_state) + "\n\n---\n\n" + final
            break
    
# ── Gradio UI ──
async def on_message(message: str, history: list, thread_id: str):
    """每次先追加用户消息，再逐次更新助手消息。同时清空输入框。"""
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": ""})
    yield history, thread_id, "" 
    last = ""
    async for display in run_agent(message, thread_id):
        last = display
        history[-1]["content"] = last
        yield history, thread_id, ""

WELCOME = """👋 你好！我是你的 **OZON 店铺助手**。

我可以帮你分析：
• 📈 经营状况与趋势
• ⚠️ 退货异常排查
• 📦 库存预警
• 📊 广告 ROI 分析

💬 试试直接输入问题。"""

with gr.Blocks(title="OZON 小帮手") as demo:
    gr.Markdown("## OZON 小帮手")
    gr.Markdown("基于真实 OZON 店铺后台数据")

    session_id = gr.State(lambda: str(uuid.uuid4()))
    chatbot = gr.Chatbot(
        value=[{"role": "assistant", "content": WELCOME}],
        height=580,
    )

    msg = gr.Textbox(
        placeholder="询问店铺数据，例如：查看上周广告 ROI",
        container=False,
        lines=1,
        max_lines=4,
        autofocus=True,
    )
    send_btn = gr.Button("发送", variant="primary")

    async def _on_submit(message, history, thread_id):
        async for result in on_message(message, history, thread_id):
            yield result

    msg.submit(_on_submit, [msg, chatbot, session_id], [chatbot, session_id, msg])
    send_btn.click(_on_submit, [msg, chatbot, session_id], [chatbot, session_id, msg])

if __name__ == "__main__":
    demo.launch(server_port=8501)
