"""
MCP streamable-http 客户端封装。
管理连接生命周期，提供工具发现和调用能力。

启动前确保 MCP Server 已运行：
  MCP_TRANSPORT=streamable-http python -m src.mcp_server.server
"""
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult


class MCPClient:
    """MCP 客户端：管理 streamable-http 连接 + ClientSession 生命周期。"""

    def __init__(self, url: str = "http://127.0.0.1:8000/mcp"):
        self.url = url
        self._session: ClientSession | None = None
        self._http_ctx = None       # streamable_http_client 的上下文
        self._session_ctx = None    # ClientSession 的上下文
        self._tools: list = []      # MCP Tool 对象列表

    # ═══════════════════════════════════════════════════════════════════
    # 连接管理
    # ═══════════════════════════════════════════════════════════════════

    async def connect(self) -> list:
        """建立连接：嵌套进入两个 async context，initialize，list_tools。
        返回 MCP Tool 对象列表。"""
        self._http_ctx = streamable_http_client(self.url)
        read, write, _ = await self._http_ctx.__aenter__()
        self._session = ClientSession(read, write)
        self._session_ctx = self._session
        await self._session_ctx.__aenter__()
        await self._session.initialize()
        result = await self._session.list_tools()
        self._tools = result.tools
        return self._tools

    async def close(self):
        """退出两个 context（先 session 后 http）。容错处理 async generator 清理。"""
        try:
            if self._session_ctx:
                await self._session_ctx.__aexit__(None, None, None)
                self._session_ctx = None
        except Exception:
            pass
        try:
            if self._http_ctx:
                await self._http_ctx.__aexit__(None, None, None)
                self._http_ctx = None
        except Exception:
            pass

    @property
    def tools(self) -> list:
        return self._tools

    # ═══════════════════════════════════════════════════════════════════
    # 工具调用
    # ═══════════════════════════════════════════════════════════════════

    async def call_tool(self, name: str, args: dict) -> dict:
        """调单个工具。返回统一格式 {data, row_count, error?}（和 tools.py 对齐）。"""
        try:
            result: CallToolResult = await self._session.call_tool(name, args)
            if result.isError:
                text = result.content[0].text if result.content else "Unknown error"
                return {"data": [], "row_count": 0, "error": text}
            text = result.content[0].text if result.content else "{}"
            return json.loads(text)
        except Exception as e:
            return {"data": [], "row_count": 0, "error": str(e)}

    async def call_tools_parallel(self, calls: list[dict]) -> dict[str, dict]:
        """并行执行多个工具调用。单个失败不影响其他。

        calls: [{"name": "get_postings", "args": {...}}, ...]
        返回 key 格式 "{tool_name}"（单次调用）或 "{tool_name}#{index}"（同名多次调用）。
        例如 plan 调了两次 get_daily_summary，key 会变成 get_daily_summary#0 和 get_daily_summary#1。
        """
        async def _call_one(idx, call):
            name = call["name"]
            args = call.get("args", {})
            return idx, name, await self.call_tool(name, args)

        tasks = [_call_one(i, c) for i, c in enumerate(calls)]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for item in results_list:
            if isinstance(item, Exception):
                output["unknown"] = {"data": [], "row_count": 0, "error": str(item)}
            else:
                idx, name, result = item
                # 同名多次调用时加序号区分，单次调用保持原名
                key = f"{name}#{idx}" if len(calls) > 1 else name
                if key not in output:
                    output[key] = result
                else:
                    # 极端情况：手动去重同名调用（不同 idx 但同名+同 key 冲突）
                    output[f"{name}#{len(output)}"] = result
        return output


# ═══════════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════════

_client: MCPClient | None = None


async def get_client() -> MCPClient:
    """获取或创建 MCPClient 单例（自动 connect）。"""
    global _client
    if _client is None:
        _client = MCPClient()
        await _client.connect()
    return _client


async def close_client() -> None:
    """关闭 MCP 客户端连接。"""
    global _client
    if _client:
        await _client.close()
        _client = None
