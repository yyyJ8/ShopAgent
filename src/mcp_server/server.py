"""
MCP Server 入口 — 注册 7 个语义化数据工具，stdio 传输。
所有工具支持可选 store_id（不传=全平台汇总，传了=单店铺过滤）。
启动：python -m src.mcp_server.server
"""

import os

from mcp.server.fastmcp import FastMCP

from . import tools as _tools

mcp = FastMCP(
    "ozon-data",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)


# ═══════════════════════════════════════════════════════════════════
# ① get_products  — 商品主数据
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_products(
    sku_ids: list[int] | None = None,
    status: str | None = None,
    is_archived: bool | None = None,
    category_id: int | None = None,
    store_id: int | None = None,
) -> dict:
    """获取商品主数据。不传 store_id = 全平台商品。

    可选参数：
    - sku_ids: SKU ID 列表，不传返回全部
    - status: 商品状态（当前均为 "price_sent"）
    - is_archived: 是否已归档
    - category_id: 类目 ID
    - store_id: 店铺 ID（不传=全平台，传了=只看该店铺）。当前店铺 1 有 39 个商品，店铺 2 有 42 个

    返回字段含 store_id，可区分每个商品属于哪个店铺。
    数据来源：products 表（原始数据，可靠度 ⭐⭐⭐）。
    """
    return await _tools.get_products(
        sku_ids=sku_ids, status=status, is_archived=is_archived, category_id=category_id, store_id=store_id,
    )


# ═══════════════════════════════════════════════════════════════════
# ② get_postings — 订单/发货数据
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_postings(
    date_start: str,
    date_end: str,
    status: str | None = None,
    delivery_schema: str | None = None,
    cancel_reason_id: int | None = None,
    store_id: int | None = None,
    limit: int = 500,
) -> dict:
    """获取订单/发货数据。不传 store_id = 全平台订单汇总。

    必填：date_start / date_end (YYYY-MM-DD，≤90天)

    可选筛选项：
    - status: "delivered"/"cancelled"/"delivering"/"awaiting_packaging"/"awaiting_deliver"
    - delivery_schema: "FBO"/"FBS"/null
    - cancel_reason_id: OZON 取消原因代码（0=未取消，常见 79/504-506/937）
    - store_id: 店铺 ID（不传=全平台）
    - limit: 最大返回条数，默认 500

    返回字段含 store_id。products 字段为 json 数组 [{sku, name, price, offer_id, quantity}]。
    数据来源：postings 表（原始数据，可靠度 ⭐⭐⭐）。
    """
    return await _tools.get_postings(
        date_start=date_start, date_end=date_end,
        status=status, delivery_schema=delivery_schema, cancel_reason_id=cancel_reason_id,
        store_id=store_id, limit=limit,
    )


# ═══════════════════════════════════════════════════════════════════
# ③ get_returns — 退货数据
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_returns(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    type: str | None = None,
    return_reason_name: str | None = None,
    store_id: int | None = None,
    limit: int = 500,
) -> dict:
    """获取退货记录。不传 store_id = 全平台退货汇总。

    必填：date_start / date_end (YYYY-MM-DD，≤90天)，按 returned_at 筛选

    可选筛选项：
    - sku_ids: SKU ID 列表
    - type: "Cancellation"（取消）/ "ClientReturn"（客户退货）
    - return_reason_name: 退货原因关键词模糊匹配（俄文）
    - store_id: 店铺 ID（不传=全平台）
    - limit: 默认 500

    返回字段含 store_id。数据来源：returns 表 LEFT JOIN products（原始数据，可靠度 ⭐⭐⭐）。
    可用于："店铺 A vs B 退货率对比？""各店铺退货原因分布？"
    """
    return await _tools.get_returns(
        date_start=date_start, date_end=date_end,
        sku_ids=sku_ids, type=type, return_reason_name=return_reason_name,
        store_id=store_id, limit=limit,
    )


# ═══════════════════════════════════════════════════════════════════
# ④ get_finance_transactions — 财务流水
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_finance_transactions(
    date_start: str,
    date_end: str,
    sku_id: int | None = None,
    operation_type: str | None = None,
    type: str | None = None,
    store_id: int | None = None,
    limit: int = 500,
) -> dict:
    """获取财务流水明细。不传 store_id = 全平台财务汇总。

    必填：date_start / date_end (YYYY-MM-DD，≤90天)

    可选筛选项：
    - sku_id: 单个 SKU ID
    - operation_type: 操作类型（如 "OperationAgentDeliveredToCustomer" 等 13 种）
    - type: 收支大类 "orders"/"returns"/"services"/"other"
    - store_id: 店铺 ID（不传=全平台）
    - limit: 默认 500

    返回字段含 store_id。services/items 为 json 数组。
    数据来源：finance_transactions 表（原始数据，可靠度 ⭐⭐⭐）。
    可用于："店铺 A 的佣金/物流成本？""店铺间费用结构对比？"
    """
    return await _tools.get_finance_transactions(
        date_start=date_start, date_end=date_end,
        sku_id=sku_id, operation_type=operation_type, type=type,
        store_id=store_id, limit=limit,
    )


# ═══════════════════════════════════════════════════════════════════
# ⑤ get_stock_snapshot — 实时库存
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_stock_snapshot(
    sku_ids: list[int] | None = None,
    source: str | None = None,
    low_stock_threshold: int | None = None,
    store_id: int | None = None,
) -> dict:
    """获取实时库存快照。不传 store_id = 全平台库存汇总。

    可选筛选项：
    - sku_ids: SKU ID 列表
    - source: 库存来源（当前均为 "fbo"）
    - low_stock_threshold: 只返回 present ≤ 此值的（断货预警）
    - store_id: 店铺 ID（不传=全平台）

    按 present 升序排列。返回字段含 store_id。
    数据来源：stocks 表 LEFT JOIN products（原始数据，可靠度 ⭐⭐⭐）。
    可用于："店铺 A 的库存健康度？""哪个店铺库存多但卖得慢？"
    """
    return await _tools.get_stock_snapshot(
        sku_ids=sku_ids, source=source, low_stock_threshold=low_stock_threshold, store_id=store_id,
    )


# ═══════════════════════════════════════════════════════════════════
# ⑥ get_ad_performance — 广告表现
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_ad_performance(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    campaign_ids: list[str] | None = None,
    campaign_state: str | None = None,
    store_id: int | None = None,
    limit: int = 500,
) -> dict:
    """获取 SKU × 广告活动 × 天的广告表现。不传 store_id = 全平台广告汇总。

    必填：date_start / date_end (YYYY-MM-DD，≤90天)

    可选筛选项：
    - sku_ids: SKU ID 列表
    - campaign_ids: 广告活动 ID 列表
    - campaign_state: "CAMPAIGN_STATE_RUNNING"/"INACTIVE"/"ARCHIVED"
    - store_id: 店铺 ID（不传=全平台）
    - limit: 默认 500

    返回字段含 store_id。数据来源：ad_sku_daily_stats LEFT JOIN ad_campaigns
    （OZON API 同步，可靠度 ⭐⭐）。
    可用于："店铺 A 的广告投产比？""店铺间广告效率对比？"
    """
    return await _tools.get_ad_performance(
        date_start=date_start, date_end=date_end,
        sku_ids=sku_ids, campaign_ids=campaign_ids, campaign_state=campaign_state,
        store_id=store_id, limit=limit,
    )


# ═══════════════════════════════════════════════════════════════════
# ⑦ get_ad_campaign_stats — 广告计划粒度日统计
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_ad_campaign_stats(
    date_start: str,
    date_end: str,
    campaign_ids: list[str] | None = None,
    store_id: int | None = None,
    limit: int = 500,
) -> dict:
    """获取广告计划粒度的日统计。不传 store_id = 全平台。

    必填：date_start / date_end (YYYY-MM-DD，≤90天)

    可选筛选项：
    - campaign_ids: 广告计划 ID 列表
    - store_id: 店铺 ID（不传=全平台）
    - limit: 默认 500

    返回 campaign 级别的 impressions/clicks/spend/orders_count/orders_sum。
    与 get_ad_performance 的区别：此工具是计划粒度，无 SKU 粒度数据。
    可用于："哪个广告计划 ROI 最高？""广告计划整体花费趋势？"
    """
    return await _tools.get_ad_campaign_stats(
        date_start=date_start, date_end=date_end,
        campaign_ids=campaign_ids, store_id=store_id, limit=limit,
    )


# ═══════════════════════════════════════════════════════════════════
# ⑧ get_daily_summary — 日汇总（⚠️ 派生数据）
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_daily_summary(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    data_quality: str | None = None,
    store_id: int | None = None,
    limit: int = 500,
) -> dict:
    """获取 SKU 天级经营汇总。不传 store_id = 全平台汇总。

    必填：date_start / date_end (YYYY-MM-DD，≤90天)

    可选筛选项：
    - sku_ids: SKU ID 列表
    - data_quality: 数据质量（当前均为 "complete"）
    - store_id: 店铺 ID（不传=全平台）
    - limit: 默认 500

    返回字段含 store_id。⚠️ ETL 派生数据，profit_margin 存在极端值。
    建议：概览/趋势/店铺对比用此工具；涉及具体金额时交叉验证原始表。
    可用于："店铺 A 的利润率趋势？""店铺 A vs B 的净利对比？"
    """
    return await _tools.get_daily_summary(
        date_start=date_start, date_end=date_end,
        sku_ids=sku_ids, data_quality=data_quality, store_id=store_id, limit=limit,
    )


# ═══════════════════════════════════════════════════════════════════
# 入口
#
# 通过 MCP_TRANSPORT 环境变量切换传输模式：
#   stdio → 给 Agent 用（Agent 通过子进程管理生命周期，默认）
#   sse   → 开发调试用（浏览器/curl 可访问）
#   http  → 生产部署用（标准 HTTP，支持多客户端）
#
# 通过 MCP_HOST / MCP_PORT 环境变量修改监听地址和端口（SSE/HTTP 模式生效）：
#   MCP_HOST=0.0.0.0 MCP_PORT=9000 MCP_TRANSPORT=sse python -m src.mcp_server.server
#
# Agent 集成配置示例（mcp_servers.json）：
#   {"ozon-data": {"command": "python", "args": ["-m", "src.mcp_server.server"], "cwd": "D:/OzonAgent"}}
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "sse":
        print(f"SSE mode: http://{mcp.settings.host}:{mcp.settings.port}/sse")
        mcp.run(transport="sse")
    elif transport == "http":
        print(f"Streamable HTTP mode: http://{mcp.settings.host}:{mcp.settings.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio
