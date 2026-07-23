"""
MCP tool schema → OpenAI function calling 格式转换。
MCP Tool.inputSchema 已是标准 JSON Schema，可直接用作 OpenAI function parameters。
"""


def adapt_tool(mcp_tool) -> dict:
    """单个 MCP Tool → OpenAI function definition。"""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "parameters": mcp_tool.inputSchema,
        },
    }


def adapt_tools(mcp_tools: list) -> list[dict]:
    """批量转换。"""
    return [adapt_tool(t) for t in mcp_tools]
