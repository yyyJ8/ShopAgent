"""
MCP 工具函数：参数校验 → 调 db → 格式化返回。
所有工具支持可选 store_id（不传=全平台，传了=单店铺）。
"""

from datetime import date

from . import db


MAX_DATE_RANGE_DAYS = 90


def _validate_date_range(date_start: str, date_end: str) -> tuple[str, str] | str:
    try:
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
    except (ValueError, TypeError):
        return f"日期格式错误，需要 YYYY-MM-DD，实际传入: start={date_start}, end={date_end}"
    if start > end:
        return f"起始日期 {date_start} 不能晚于结束日期 {date_end}"
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        return f"日期范围 {(end - start).days} 天，超过最大限制 {MAX_DATE_RANGE_DAYS} 天"
    return (date_start, date_end)


def _format_result(tool_name: str, args: dict, rows: list[dict]) -> dict:
    return {"tool": tool_name, "args": args, "row_count": len(rows), "data": rows}


def _format_error(tool_name: str, args: dict, error: str) -> dict:
    return {"tool": tool_name, "args": args, "row_count": 0, "data": [], "error": error}


def _check_sku_ids(sku_ids) -> str | None:
    if sku_ids is not None:
        if not isinstance(sku_ids, list) or not all(isinstance(x, int) for x in sku_ids):
            return "sku_ids 必须是整数列表"
    return None


# ═══════════════════════════════════════════════════════════════════
# ① get_products
# ═══════════════════════════════════════════════════════════════════

async def get_products(
    sku_ids: list[int] | None = None,
    status: str | None = None,
    is_archived: bool | None = None,
    category_id: int | None = None,
    store_id: int | None = None,
) -> dict:
    args = dict(sku_ids=sku_ids, status=status, is_archived=is_archived, category_id=category_id, store_id=store_id)
    if err := _check_sku_ids(sku_ids): return _format_error("get_products", args, err)
    try:
        rows = await db.query_products(sku_ids=sku_ids, status=status, is_archived=is_archived, category_id=category_id, store_id=store_id)
        return _format_result("get_products", args, rows)
    except Exception as e:
        return _format_error("get_products", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ② get_postings
# ═══════════════════════════════════════════════════════════════════

async def get_postings(
    date_start: str,
    date_end: str,
    status: str | None = None,
    delivery_schema: str | None = None,
    cancel_reason_id: int | None = None,
    store_id: int | None = None,
    limit: int = db.DEFAULT_LIMIT,
) -> dict:
    args = dict(date_start=date_start, date_end=date_end, status=status, delivery_schema=delivery_schema, cancel_reason_id=cancel_reason_id, store_id=store_id, limit=limit)
    if isinstance(v := _validate_date_range(date_start, date_end), str): return _format_error("get_postings", args, v)
    try:
        rows = await db.query_postings(date_start=date_start, date_end=date_end, status=status, delivery_schema=delivery_schema, cancel_reason_id=cancel_reason_id, store_id=store_id, limit=limit)
        return _format_result("get_postings", args, rows)
    except Exception as e:
        return _format_error("get_postings", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ③ get_returns
# ═══════════════════════════════════════════════════════════════════

async def get_returns(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    type: str | None = None,
    return_reason_name: str | None = None,
    store_id: int | None = None,
    limit: int = db.DEFAULT_LIMIT,
) -> dict:
    args = dict(date_start=date_start, date_end=date_end, sku_ids=sku_ids, type=type, return_reason_name=return_reason_name, store_id=store_id, limit=limit)
    if isinstance(v := _validate_date_range(date_start, date_end), str): return _format_error("get_returns", args, v)
    if err := _check_sku_ids(sku_ids): return _format_error("get_returns", args, err)
    try:
        rows = await db.query_returns(date_start=date_start, date_end=date_end, sku_ids=sku_ids, type_filter=type, return_reason_name=return_reason_name, store_id=store_id, limit=limit)
        return _format_result("get_returns", args, rows)
    except Exception as e:
        return _format_error("get_returns", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ④ get_finance_transactions
# ═══════════════════════════════════════════════════════════════════

async def get_finance_transactions(
    date_start: str,
    date_end: str,
    sku_id: int | None = None,
    operation_type: str | None = None,
    type: str | None = None,
    store_id: int | None = None,
    limit: int = db.DEFAULT_LIMIT,
) -> dict:
    args = dict(date_start=date_start, date_end=date_end, sku_id=sku_id, operation_type=operation_type, type=type, store_id=store_id, limit=limit)
    if isinstance(v := _validate_date_range(date_start, date_end), str): return _format_error("get_finance_transactions", args, v)
    try:
        rows = await db.query_finance_transactions(date_start=date_start, date_end=date_end, sku_id=sku_id, operation_type=operation_type, type_filter=type, store_id=store_id, limit=limit)
        return _format_result("get_finance_transactions", args, rows)
    except Exception as e:
        return _format_error("get_finance_transactions", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ⑤ get_stock_snapshot
# ═══════════════════════════════════════════════════════════════════

async def get_stock_snapshot(
    sku_ids: list[int] | None = None,
    source: str | None = None,
    low_stock_threshold: int | None = None,
    store_id: int | None = None,
) -> dict:
    args = dict(sku_ids=sku_ids, source=source, low_stock_threshold=low_stock_threshold, store_id=store_id)
    if err := _check_sku_ids(sku_ids): return _format_error("get_stock_snapshot", args, err)
    try:
        rows = await db.query_stock_snapshot(sku_ids=sku_ids, source=source, low_stock_threshold=low_stock_threshold, store_id=store_id)
        return _format_result("get_stock_snapshot", args, rows)
    except Exception as e:
        return _format_error("get_stock_snapshot", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ⑥ get_ad_performance
# ═══════════════════════════════════════════════════════════════════

async def get_ad_performance(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    campaign_ids: list[str] | None = None,
    campaign_state: str | None = None,
    store_id: int | None = None,
    limit: int = db.DEFAULT_LIMIT,
) -> dict:
    args = dict(date_start=date_start, date_end=date_end, sku_ids=sku_ids, campaign_ids=campaign_ids, campaign_state=campaign_state, store_id=store_id, limit=limit)
    if isinstance(v := _validate_date_range(date_start, date_end), str): return _format_error("get_ad_performance", args, v)
    if err := _check_sku_ids(sku_ids): return _format_error("get_ad_performance", args, err)
    try:
        rows = await db.query_ad_performance(date_start=date_start, date_end=date_end, sku_ids=sku_ids, campaign_ids=campaign_ids, campaign_state=campaign_state, store_id=store_id, limit=limit)
        return _format_result("get_ad_performance", args, rows)
    except Exception as e:
        return _format_error("get_ad_performance", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ⑦ get_ad_campaign_stats
# ═══════════════════════════════════════════════════════════════════

async def get_ad_campaign_stats(
    date_start: str,
    date_end: str,
    campaign_ids: list[str] | None = None,
    store_id: int | None = None,
    limit: int = db.DEFAULT_LIMIT,
) -> dict:
    args = dict(date_start=date_start, date_end=date_end, campaign_ids=campaign_ids, store_id=store_id, limit=limit)
    if isinstance(v := _validate_date_range(date_start, date_end), str): return _format_error("get_ad_campaign_stats", args, v)
    try:
        rows = await db.query_ad_campaign_stats(date_start=date_start, date_end=date_end, campaign_ids=campaign_ids, store_id=store_id, limit=limit)
        return _format_result("get_ad_campaign_stats", args, rows)
    except Exception as e:
        return _format_error("get_ad_campaign_stats", args, str(e))


# ═══════════════════════════════════════════════════════════════════
# ⑧ get_daily_summary
# ═══════════════════════════════════════════════════════════════════

async def get_daily_summary(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    data_quality: str | None = None,
    store_id: int | None = None,
    limit: int = db.DEFAULT_LIMIT,
) -> dict:
    args = dict(date_start=date_start, date_end=date_end, sku_ids=sku_ids, data_quality=data_quality, store_id=store_id, limit=limit)
    if isinstance(v := _validate_date_range(date_start, date_end), str): return _format_error("get_daily_summary", args, v)
    if err := _check_sku_ids(sku_ids): return _format_error("get_daily_summary", args, err)
    try:
        rows = await db.query_daily_summary(date_start=date_start, date_end=date_end, sku_ids=sku_ids, data_quality=data_quality, store_id=store_id, limit=limit)
        return _format_result("get_daily_summary", args, rows)
    except Exception as e:
        return _format_error("get_daily_summary", args, str(e))
