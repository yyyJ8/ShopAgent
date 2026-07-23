"""
Agent 命令行交互入口。

启动方式（需要两个终端）：
  终端 1: MCP_TRANSPORT=http python -m src.mcp_server.server
  终端 2: python -m src.agent.run
"""
import asyncio

from langchain_core.messages import HumanMessage

from .config_loader import load_config
from .graph import build_graph
from .mcp_client import close_client
from .state import AgentState


async def main():
    print("正在连接 MCP Server...")
    try:
        graph = await build_graph()
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("请确保 MCP Server 已启动: MCP_TRANSPORT=http python -m src.mcp_server.server")
        return

    print("✅ Agent 就绪。输入问题开始（q 退出）。\n")

    while True:
        try:
            query = input("🔍 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if query.lower() in ("q", "exit", "quit"):
            break
        if not query:
            continue

        initial_state: AgentState = {
            "user_query": query,
            "messages": [HumanMessage(content=query)],
            "config": load_config(),
            "error": "",
        }

        try:
            result = await graph.ainvoke(
                initial_state,
                {"configurable": {"thread_id": "cli-session"}},
            )
            final = result.get("final_answer", "(无输出)")
            print(f"\n{final}\n")
        except Exception as e:
            print(f"\n❌ 出错: {e}\n")
        print("-" * 60)

    await close_client()
    print("再见。")


if __name__ == "__main__":
    asyncio.run(main())
