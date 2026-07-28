"""
将 MCP 工具返回的原始数据压缩为 LLM 友好的摘要，降低 token 消耗。

analyze 节点：全量数据 → 聚合统计 + Top/Bottom N
detect 节点：只提取异常 SKU 的相关数据行
"""

# ── 哪些字段是数值型（可以求和 / 求均值）──
# 不在这个集合里的字段视为标签/文本，不做数值聚合
_NUMERIC_FIELDS = {
    "revenue", "net_profit", "ordered_units", "delivered_units",
    "returns_units", "returns_count", "stock_present", "stock_reserved",
    "present", "reserved", "spend", "impressions", "clicks",
    "sold_units", "orders_count", "orders_sum",
    "profit_margin", "drr_total", "drr_promotion", "ctr", "avg_cpc",
    "quantity", "price", "commission", "amount", "cost",
}

# 这些字段在按 SKU 聚合时取末值（比例/快照），不累加
_NO_SUM_FIELDS = {
    "profit_margin", "drr_total", "drr_promotion", "ctr", "avg_cpc",
    "present", "stock_present", "stock_reserved",
    "data_quality", "product_status", "sku_price", "campaign_state",
}

TOP_N = 5  # Top/Bottom 列表长度


def _num(v):
    """安全转 float，不可转返回 None。"""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _sku_key(row: dict) -> int | None:
    """从一行数据里提取 sku_id（兼容 sku 列名）。"""
    return row.get("sku_id") or row.get("sku")


def _aggregate_by_sku(rows: list[dict]) -> dict[int, dict]:
    """按 sku_id 聚合：数值字段累加，比例/快照字段取末值。"""
    grouped: dict[int, dict] = {}
    for row in rows:
        sku = _sku_key(row)
        if sku is None:
            continue
        if sku not in grouped:
            grouped[sku] = {}
        for k, v in row.items():
            if v is None:
                continue
            if k in _NO_SUM_FIELDS:
                grouped[sku][k] = v
            elif k in _NUMERIC_FIELDS:
                n = _num(v)
                if n is not None:
                    grouped[sku][k] = grouped[sku].get(k, 0) + n
            else:
                grouped[sku][k] = v
    return grouped


def _pick_sort_key(fields: dict) -> float:
    """选最重要的数值字段做排序键。优先级：revenue > spend > present > ordered_units > 首个数值字段。"""
    for key in ("revenue", "spend", "present", "ordered_units", "returns_units"):
        n = _num(fields.get(key))
        if n is not None and n != 0:
            return -n  # 负数 = 降序
    # fallback：找任意非零数值字段
    for k, v in fields.items():
        n = _num(v)
        if n is not None and n != 0:
            return -n
    return 0


def _compact_sku(fields: dict) -> dict:
    """将一个 SKU 的字段压缩：只保留数值字段（四舍五入）+ 非空标签字段。"""
    compact = {}
    for k, v in fields.items():
        if v is None:
            continue
        if k in _NUMERIC_FIELDS and k not in _NO_SUM_FIELDS:
            n = _num(v)
            compact[k] = round(n, 2) if n is not None else v
        elif k in _NO_SUM_FIELDS:
            n = _num(v)
            compact[k] = round(n, 2) if n is not None else v
        elif isinstance(v, str) and v:
            compact[k] = v
    return compact


def _summarize_rows(rows: list[dict], tool_name: str = "") -> dict:
    """给定一个数据源的原始行，产出自适应摘要。"""
    if not rows:
        return {"row_count": 0}

    skus = {_sku_key(r) for r in rows if _sku_key(r) is not None}
    by_sku = _aggregate_by_sku(rows)

    # ── 全局汇总（按 SKU 跨天已聚合，这里汇总所有数值字段，包括 stock 快照）──
    totals: dict[str, float] = {}
    _no_sum_for_totals = frozenset({
        "profit_margin", "drr_total", "drr_promotion", "ctr", "avg_cpc",
        "data_quality",
    })
    for fields in by_sku.values():
        for k, v in fields.items():
            if k in _NUMERIC_FIELDS and k not in _no_sum_for_totals:
                n = _num(v)
                if n is not None:
                    totals[k] = totals.get(k, 0) + n

    # 重算比例字段
    if totals.get("revenue") and totals.get("net_profit"):
        totals["profit_margin"] = round(totals["net_profit"] / totals["revenue"] * 100, 1)
    if totals.get("spend") and totals.get("total_ordered"):
        totals["drr_total"] = round(totals["spend"] / totals["total_ordered"] * 100, 1)
    if totals.get("returns_units") and totals.get("ordered_units"):
        totals["return_rate"] = round(totals["returns_units"] / totals["ordered_units"] * 100, 1)

    # ── 可用列名（帮 LLM 理解数据结构）──
    sample = rows[0]
    columns = [k for k in sample.keys() if not k.startswith("_")]

    # ── 每个 SKU 紧凑摘要 ──
    sku_list = []
    for sku, fields in sorted(by_sku.items(), key=lambda x: _pick_sort_key(x[1])):
        compact = _compact_sku(fields)
        compact["sku_id"] = sku
        sku_list.append(compact)
        if len(sku_list) >= 30:  # 硬上限，防止 token 爆炸
            break

    return {
        "tool": tool_name,
        "row_count": len(rows),
        "sku_count": len(skus),
        "columns": columns,
        "totals": {k: round(v, 2) if isinstance(v, float) else v for k, v in totals.items()},
        "skus": sku_list,
    }


def summarize_for_analysis(tool_results: dict) -> dict:
    """将 tool_results 压缩为 LLM 友好的分析摘要。"""
    summary = {}
    for key, result in tool_results.items():
        if result.get("error"):
            summary[key] = {"error": result["error"]}
            continue
        data = result.get("data", [])
        tool_name = result.get("tool", key.split("#")[0])
        summary[key] = {
            "args": result.get("args", {}),
            **_summarize_rows(data, tool_name),
        }
    return summary


def extract_anomaly_context(tool_results: dict, anomalies: list) -> dict:
    """只提取异常 SKU 相关的数据行，供 detect 归因使用。

    返回 {"tool_name#n": {"data": [仅关联行], "row_count": N}, ...}
    """
    # 收集所有异常涉及的 sku_id
    anomaly_skus: set[int] = set()
    for a in anomalies:
        sid = a.get("sku_id")
        if sid is not None:
            anomaly_skus.add(int(sid))

    if not anomaly_skus:
        return {}

    context = {}
    for key, result in tool_results.items():
        if result.get("error"):
            continue
        data = result.get("data", [])
        relevant = [r for r in data if _sku_key(r) in anomaly_skus]
        if relevant:
            context[key] = {
                "tool": result.get("tool", key.split("#")[0]),
                "data": relevant,
                "row_count": len(relevant),
            }
    return context
