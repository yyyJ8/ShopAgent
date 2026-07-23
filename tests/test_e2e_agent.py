"""Agent 端到端快速验证。MCP Server 必须先启动。"""
import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from langchain_core.messages import HumanMessage

from src.agent.config_loader import load_config
from src.agent.graph import build_graph
from src.agent.state import AgentState


async def test(query: str, label: str):
    print(f"\n{'=' * 60}")
    print(f"Test: {label}")
    print(f"Query: {query}")
    print(f"{'=' * 60}")

    g = await build_graph()

    state: AgentState = {
        "user_query": query,
        "messages": [HumanMessage(content=query)],
        "config": load_config(),
        "error": "",
    }

    result = await g.ainvoke(state, {"configurable": {"thread_id": label}})
    print(f"intent: {result.get('intent')}")
    tr = result.get("tool_results", {})
    print(f"tools called: {list(tr.keys())}")
    for n, r in tr.items():
        print(f"  {n}: rows={r.get('row_count', '?')}, err={r.get('error', '-')}")
    print(f"final_answer:\n{result.get('final_answer', '(none)')[:800]}")


async def main():
    await test("你好，你能做什么？", "chat")
    await test("查询最近7天订单量", "lookup")


if __name__ == "__main__":
    asyncio.run(main())
