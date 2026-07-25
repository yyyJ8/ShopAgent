"""测试 _check_threshold 聚合逻辑：日粒度 → sum。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re

# ── 从 graph.py 提取的核心逻辑副本 ──

_NO_SUM_FIELDS = frozenset({
    "profit_margin", "drr_total", "drr_promotion", "ctr", "avg_cpc",
    "present", "stock_present", "stock_reserved",
    "data_quality", "product_status", "sku_price", "campaign_state",
})

OPS = {
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "lt":  lambda a, b: a < b,
    "gt":  lambda a, b: a > b,
    "eq":  lambda a, b: a == b,
}


def _resolve_severity(fields, severity_map, ref_field):
    if not severity_map or not ref_field:
        return "warning"
    field_val = fields.get(ref_field)
    if field_val is None:
        return "warning"
    OP_MAP = {
        "<":  lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">":  lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
    }
    for level, desc in severity_map.items():
        m = re.search(r"([<>]=?)\s*(-?\d+\.?\d*)", desc)
        if not m:
            continue
        op_str, val_str = m.group(1), m.group(2)
        try:
            target = float(val_str)
        except ValueError:
            continue
        if OP_MAP.get(op_str, lambda a, b: False)(field_val, target):
            return level
    return "warning"


def check_threshold(tool_results, rule_config, rule_name=""):
    """graph.py _check_threshold 的副本，用于独立测试。"""
    data_source_str = rule_config.get("data_source", "")
    source_names = [s.strip() for s in data_source_str.split("+")]

    # 收集
    rows_by_sku = {}
    for source_name in source_names:
        for key, result in tool_results.items():
            if not key.startswith(source_name):
                continue
            if result.get("error"):
                continue
            for row in result.get("data", []):
                sku_id = row.get("sku_id") or row.get("sku")
                if sku_id is None:
                    continue
                rows_by_sku.setdefault(sku_id, []).append(row)

    if not rows_by_sku:
        return []

    # 聚合
    merged = {}
    for sku_id, rows in rows_by_sku.items():
        combined = {}
        for row in rows:
            for k, v in row.items():
                if v is None:
                    continue
                if k in _NO_SUM_FIELDS or not isinstance(v, (int, float)):
                    combined[k] = v
                else:
                    combined[k] = combined.get(k, 0) + v
        merged[sku_id] = combined

    conditions = rule_config.get("conditions", [])
    require_all = rule_config.get("require", "all") == "all"
    severity_map = rule_config.get("severity_map", {})
    ref_field = conditions[0]["field"] if conditions else None

    anomalies = []
    missing_count = {}
    required_fields = {c["field"] for c in conditions}
    for sku_id, fields in merged.items():
        hits = []
        for cond in conditions:
            actual = fields.get(cond["field"])
            if actual is None:
                hits.append(False)
                continue
            try:
                hits.append(OPS.get(cond["op"], lambda a, b: False)(actual, cond["value"]))
            except (TypeError, ValueError):
                hits.append(False)
        if require_all and not all(hits):
            for f in required_fields - set(fields.keys()):
                missing_count[f] = missing_count.get(f, 0) + 1
            continue
        severity = _resolve_severity(fields, severity_map, ref_field)
        anomalies.append({
            "type": rule_name, "severity": severity, "sku_id": sku_id,
            "detail": {c["field"]: fields.get(c["field"]) for c in conditions},
            "description": rule_config.get("description", ""),
        })
    return anomalies


# ═══════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════

def test_sum():
    """A: 多天数值累加"""
    tr = {"get_daily_summary": {"data": [
        {"sku_id": 1, "ordered_units": 3, "stock_present": 10},
        {"sku_id": 1, "ordered_units": 5, "stock_present": 8},
        {"sku_id": 1, "ordered_units": 2, "stock_present": 6},
    ], "row_count": 3}}
    rule = {"type": "阈值", "require": "all", "data_source": "get_daily_summary",
            "conditions": [{"field": "ordered_units", "op": "gte", "value": 10}],
            "description": ""}
    r = check_threshold(tr, rule)
    assert len(r) == 1 and r[0]["detail"]["ordered_units"] == 10, f"FAIL: {r}"
    print("A  pass: sum 3+5+2=10")


def test_snapshot():
    """B: 快照/比例取末值"""
    tr = {"get_daily_summary": {"data": [
        {"sku_id": 1, "stock_present": 10, "profit_margin": 50},
        {"sku_id": 1, "stock_present": 8,  "profit_margin": 45},
        {"sku_id": 1, "stock_present": 6,  "profit_margin": 55},
    ], "row_count": 3}}
    rule = {"type": "阈值", "require": "all", "data_source": "get_daily_summary",
            "conditions": [
                {"field": "stock_present", "op": "lte", "value": 10},
                {"field": "profit_margin", "op": "gte", "value": 50},
            ], "description": ""}
    r = check_threshold(tr, rule)
    assert len(r) == 1, f"FAIL count: {r}"
    assert r[0]["detail"]["stock_present"] == 6, f"stock should be 6: {r}"
    assert r[0]["detail"]["profit_margin"] == 55, f"margin should be 55: {r}"
    print("B  pass: stock=6 (last), margin=55 (last)")


def test_single_day():
    """C: 单天数据 sum 就是自己"""
    tr = {"get_daily_summary": {"data": [
        {"sku_id": 1, "ordered_units": 8},
    ], "row_count": 1}}
    rule = {"type": "阈值", "require": "all", "data_source": "get_daily_summary",
            "conditions": [{"field": "ordered_units", "op": "gte", "value": 5}],
            "description": ""}
    r = check_threshold(tr, rule)
    assert len(r) == 1 and r[0]["detail"]["ordered_units"] == 8
    print("C  pass: single day 8=8")


def test_cross_source():
    """D: 跨源聚合：两个数据源的数值各自 sum 后合并"""
    tr = {
        "get_daily_summary": {"data": [
            {"sku_id": 1, "ordered_units": 2},
            {"sku_id": 1, "ordered_units": 3},
        ], "row_count": 2},
        "get_ad_performance": {"data": [
            {"sku_id": 1, "spend": 200, "ctr": 5},
            {"sku_id": 1, "spend": 400, "ctr": 3},
        ], "row_count": 2},
    }
    rule = {"type": "阈值", "require": "all",
            "data_source": "get_daily_summary + get_ad_performance",
            "conditions": [
                {"field": "ordered_units", "op": "gte", "value": 5},
                {"field": "spend", "op": "gte", "value": 500},
            ], "description": ""}
    r = check_threshold(tr, rule)
    assert len(r) == 1, f"FAIL count: {r}"
    assert r[0]["detail"]["ordered_units"] == 5
    assert r[0]["detail"]["spend"] == 600
    print("D  pass: cross-source sum ordered_units=5, spend=600")


if __name__ == "__main__":
    test_sum()
    test_snapshot()
    test_single_day()
    test_cross_source()
    print("OK all 4 tests passed")
