"""
PostgreSQL 连接池 + 固定 SQL 查询。
每张业务表一个查询函数，参数化防注入，只做 SELECT + WHERE + ORDER BY。
所有查询支持可选 store_id 过滤（不传=全平台，传了=单店铺）。
"""

import json
import os
from datetime import date as dt_date
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _d(s: str) -> dt_date:
    """字符串 → datetime.date，asyncpg 不接受字符串类型的日期参数。"""
    return dt_date.fromisoformat(s)


async def _init_conn(conn: asyncpg.Connection) -> None:
    """注册 json/jsonb 编解码器。"""
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            min_size=2,
            max_size=10,
            command_timeout=30,
            init=_init_conn,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


DEFAULT_LIMIT = 500


# ═══════════════════════════════════════════════════════════════════
# ① get_products
# ═══════════════════════════════════════════════════════════════════

_PRODUCTS_SQL = """
    SELECT sku_id, product_id, name, offer_id, category_id,
           price, old_price, min_price, commission_fbo_pct,
           volume_weight, status, is_archived,
           primary_image, images,
           created_at, updated_at, store_id
    FROM ozon.products
    WHERE ($1::bigint[] IS NULL OR sku_id = ANY($1))
      AND ($2::varchar IS NULL OR status = $2)
      AND ($3::boolean IS NULL OR is_archived = $3)
      AND ($4::int IS NULL OR category_id = $4)
      AND ($5::int IS NULL OR store_id = $5)
    ORDER BY sku_id
"""


async def query_products(
    sku_ids: list[int] | None = None,
    status: str | None = None,
    is_archived: bool | None = None,
    category_id: int | None = None,
    store_id: int | None = None,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(_PRODUCTS_SQL, sku_ids, status, is_archived, category_id, store_id)
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ② get_postings
# ═══════════════════════════════════════════════════════════════════

_POSTINGS_SQL = """
    SELECT posting_number, order_number, delivery_schema,
           status, cancel_reason_id,
           created_at, in_process_at, delivered_at,
           products, store_id
    FROM ozon.postings
    WHERE created_at BETWEEN $1 AND $2
      AND ($3::varchar IS NULL OR status = $3)
      AND ($4::varchar IS NULL OR delivery_schema = $4)
      AND ($5::int IS NULL OR cancel_reason_id = $5)
      AND ($6::int IS NULL OR store_id = $6)
    ORDER BY created_at DESC
    LIMIT $7
"""


async def query_postings(
    date_start: str,
    date_end: str,
    status: str | None = None,
    delivery_schema: str | None = None,
    cancel_reason_id: int | None = None,
    store_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        _POSTINGS_SQL,
        _d(date_start), _d(date_end), status, delivery_schema, cancel_reason_id, store_id, limit,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ③ get_returns
# ═══════════════════════════════════════════════════════════════════

_RETURNS_SQL = """
    SELECT r.id, r.posting_number, r.sku,
           r.type, r.return_reason_name,
           r.quantity, r.price,
           r.visual_status,
           r.status_changed_at, r.returned_at, r.finished_at,
           r.schema AS delivery_schema,
           p.name AS product_name,
           r.store_id
    FROM ozon.returns r
    LEFT JOIN ozon.products p ON r.sku = p.sku_id AND r.store_id = p.store_id
    WHERE r.returned_at BETWEEN $1 AND $2
      AND ($3::bigint[] IS NULL OR r.sku = ANY($3))
      AND ($4::varchar IS NULL OR r.type = $4)
      AND ($5::varchar IS NULL OR r.return_reason_name ILIKE '%' || $5 || '%')
      AND ($6::int IS NULL OR r.store_id = $6)
    ORDER BY r.returned_at DESC
    LIMIT $7
"""


async def query_returns(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    type_filter: str | None = None,
    return_reason_name: str | None = None,
    store_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        _RETURNS_SQL,
        _d(date_start), _d(date_end), sku_ids, type_filter, return_reason_name, store_id, limit,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ④ get_finance_transactions
# ═══════════════════════════════════════════════════════════════════

_FINANCE_SQL = """
    SELECT operation_id, operation_type, operation_type_name,
           type, operation_date,
           sku_id, item_name, posting_number, delivery_schema,
           amount, accruals_for_sale, sale_commission,
           delivery_charge, return_delivery_charge,
           services, items, store_id
    FROM ozon.finance_transactions
    WHERE operation_date BETWEEN $1 AND $2
      AND ($3::bigint IS NULL OR sku_id = $3)
      AND ($4::varchar IS NULL OR operation_type = $4)
      AND ($5::varchar IS NULL OR type = $5)
      AND ($6::int IS NULL OR store_id = $6)
    ORDER BY operation_date DESC, amount DESC
    LIMIT $7
"""


async def query_finance_transactions(
    date_start: str,
    date_end: str,
    sku_id: int | None = None,
    operation_type: str | None = None,
    type_filter: str | None = None,
    store_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        _FINANCE_SQL,
        _d(date_start), _d(date_end), sku_id, operation_type, type_filter, store_id, limit,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ⑤ get_stock_snapshot
# ═══════════════════════════════════════════════════════════════════

_STOCK_SQL = """
    SELECT s.sku_id, p.name, p.offer_id, p.status AS product_status,
           s.source,
           s.present, s.reserved,
           s.updated_at, s.store_id
    FROM ozon.stocks s
    LEFT JOIN ozon.products p ON s.sku_id = p.sku_id AND s.store_id = p.store_id
    WHERE ($1::bigint[] IS NULL OR s.sku_id = ANY($1))
      AND ($2::varchar IS NULL OR s.source = $2)
      AND ($3::int IS NULL OR s.present <= $3)
      AND ($4::int IS NULL OR s.store_id = $4)
    ORDER BY s.present ASC
"""


async def query_stock_snapshot(
    sku_ids: list[int] | None = None,
    source: str | None = None,
    low_stock_threshold: int | None = None,
    store_id: int | None = None,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(_STOCK_SQL, sku_ids, source, low_stock_threshold, store_id)
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ⑥ get_ad_performance
# ═══════════════════════════════════════════════════════════════════

_AD_PERFORMANCE_SQL = """
    SELECT a.stat_date, a.campaign_id,
           c.title AS campaign_title, c.campaign_type, c.state AS campaign_state,
           c.budget AS campaign_budget,
           a.sku_id, a.sku_name, a.sku_price,
           a.impressions, a.clicks, a.ctr,
           a.add_to_cart, a.avg_cpc,
           a.spend,
           a.sold_units, a.sales_promotion, a.total_ordered,
           a.drr_promotion, a.drr_total,
           a.date_added, a.store_id
    FROM ozon.ad_sku_daily_stats a
    LEFT JOIN ozon.ad_campaigns c ON a.campaign_id = c.campaign_id AND a.store_id = c.store_id
    WHERE a.stat_date BETWEEN $1 AND $2
      AND ($3::bigint[] IS NULL OR a.sku_id = ANY($3))
      AND ($4::varchar[] IS NULL OR a.campaign_id = ANY($4))
      AND ($5::varchar IS NULL OR c.state = $5)
      AND ($6::int IS NULL OR a.store_id = $6)
    ORDER BY a.stat_date, a.spend DESC
    LIMIT $7
"""


async def query_ad_performance(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    campaign_ids: list[str] | None = None,
    campaign_state: str | None = None,
    store_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        _AD_PERFORMANCE_SQL,
        _d(date_start), _d(date_end), sku_ids, campaign_ids, campaign_state, store_id, limit,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ⑦ get_ad_campaign_stats
# ═══════════════════════════════════════════════════════════════════

_AD_CAMPAIGN_STATS_SQL = """
    SELECT campaign_id, stat_date,
           impressions, clicks, spend,
           orders_count, orders_sum,
           synced_at, store_id
    FROM ozon.ad_daily_stats
    WHERE stat_date BETWEEN $1 AND $2
      AND ($3::varchar[] IS NULL OR campaign_id = ANY($3))
      AND ($4::int IS NULL OR store_id = $4)
    ORDER BY stat_date, spend DESC
    LIMIT $5
"""


async def query_ad_campaign_stats(
    date_start: str,
    date_end: str,
    campaign_ids: list[str] | None = None,
    store_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        _AD_CAMPAIGN_STATS_SQL,
        _d(date_start), _d(date_end), campaign_ids, store_id, limit,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# ⑧ get_daily_summary
# ═══════════════════════════════════════════════════════════════════

_DAILY_SUMMARY_SQL = """
    SELECT d.date, d.sku_id, p.name, p.offer_id, p.status AS product_status,
           d.ordered_units, d.delivered_units, d.returns_units, d.cancelled_units,
           d.revenue, d.returns_amount,
           d.commissions, d.logistics_costs, d.storage_fees,
           d.advertising, d.promotion_costs, d.other_costs,
           d.net_profit, d.profit_margin,
           d.stock_present, d.stock_reserved,
           d.data_quality, d.store_id
    FROM ozon.sku_daily_summary d
    LEFT JOIN ozon.products p ON d.sku_id = p.sku_id AND d.store_id = p.store_id
    WHERE d.date BETWEEN $1 AND $2
      AND ($3::bigint[] IS NULL OR d.sku_id = ANY($3))
      AND ($4::varchar IS NULL OR d.data_quality = $4)
      AND ($5::int IS NULL OR d.store_id = $5)
    ORDER BY d.date, d.net_profit DESC
    LIMIT $6
"""


async def query_daily_summary(
    date_start: str,
    date_end: str,
    sku_ids: list[int] | None = None,
    data_quality: str | None = None,
    store_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        _DAILY_SUMMARY_SQL, _d(date_start), _d(date_end), sku_ids, data_quality, store_id, limit,
    )
    return [dict(r) for r in rows]
