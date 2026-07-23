"""
MCP 工具快速验证：逐工具调用，确认 SQL 正确 + DB 连通。
"""

import asyncio
import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows 终端编码修复
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.mcp_server import tools


TODAY = date.today().isoformat()
WEEK_AGO = (date.today() - timedelta(days=7)).isoformat()
MONTH_AGO = (date.today() - timedelta(days=30)).isoformat()


async def test_tool(name: str, coro) -> None:
    print(f"\n{'='*60}")
    print(f"🔧 {name}")
    print(f"{'='*60}")
    try:
        result = await coro
        print(f"  row_count: {result.get('row_count', '?')}")
        if result.get("error"):
            print(f"  ⚠️ error: {result['error']}")
        if result.get("data"):
            # 只打印第一条的 keys 和前两条数据
            print(f"  字段 ({len(result['data'][0])} 个): {list(result['data'][0].keys())[:10]}...")
            for i, row in enumerate(result["data"][:2]):
                # 截断过长的值
                short = {k: (str(v)[:80] if v is not None else None) for k, v in list(row.items())[:8]}
                print(f"  row[{i}]: {json.dumps(short, ensure_ascii=False, default=str)}")
    except Exception as e:
        print(f"  ❌ 异常: {type(e).__name__}: {e}")


async def main():
    print(f"📅 测试日期范围: {WEEK_AGO} ~ {TODAY}")
    print(f"📅 长日期范围: {MONTH_AGO} ~ {TODAY}")

    # ① get_products
    await test_tool("get_products (全部)", tools.get_products())
    await test_tool("get_products (status=active)", tools.get_products(status="active"))

    # ② get_postings
    await test_tool("get_postings (最近7天)", tools.get_postings(WEEK_AGO, TODAY))
    await test_tool("get_postings (cancelled)", tools.get_postings(WEEK_AGO, TODAY, status="cancelled"))

    # ③ get_returns
    await test_tool("get_returns (最近30天)", tools.get_returns(MONTH_AGO, TODAY))

    # ④ get_finance_transactions
    await test_tool("get_finance_transactions (最近7天)", tools.get_finance_transactions(WEEK_AGO, TODAY))

    # ⑤ get_stock_snapshot
    await test_tool("get_stock_snapshot (全部)", tools.get_stock_snapshot())
    await test_tool("get_stock_snapshot (low_stock≤10)", tools.get_stock_snapshot(low_stock_threshold=10))

    # ⑥ get_ad_performance
    await test_tool("get_ad_performance (最近7天)", tools.get_ad_performance(WEEK_AGO, TODAY))

    # ⑦ get_daily_summary
    await test_tool("get_daily_summary (最近7天)", tools.get_daily_summary(WEEK_AGO, TODAY))

    # 校验测试
    await test_tool("校验: 日期格式错误", tools.get_postings("bad", "date"))
    await test_tool("校验: 范围超90天", tools.get_postings("2026-01-01", "2026-07-22"))

    # 关闭连接池
    from src.mcp_server import db
    await db.close_pool()
    print("\n✅ 全部测试完成。")


if __name__ == "__main__":
    asyncio.run(main())
