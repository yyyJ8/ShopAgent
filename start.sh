#!/bin/bash
# OZON Agent 一键启动脚本
# 用法：bash start.sh

set -e

# 激活虚拟环境
source venv/Scripts/activate

# 启动 MCP Server（后台）
echo "▶ 启动 MCP Server..."
MCP_TRANSPORT=streamable-http MCP_PORT=8000 python -m src.mcp_server.server &
MCP_PID=$!
echo "  MCP Server PID: $MCP_PID"

# 等待 MCP Server 就绪
sleep 2

# 启动 Gradio
echo "▶ 启动 Gradio..."
python -m src.gradio_app

# 退出时清理
kill $MCP_PID 2>/dev/null
echo "▶ 已停止"
