"""
数据库探索脚本：读取 .env 配置，连接 PostgreSQL，输出表结构和字段信息到 output/ 目录。
不查询实际数据，只看 schema。
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# 路径
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# 加载项目根目录的 .env
load_dotenv(BASE_DIR.parent / ".env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}


def log(msg: str, lines: list[str]) -> None:
    """同时打印到控制台 + 收集到 lines 列表。"""
    print(msg)
    lines.append(msg)


async def explore() -> None:
    lines: list[str] = []
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = await asyncpg.connect(**DB_CONFIG)
    log(f"✅ 已连接: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}", lines)
    log(f"⏰ 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", lines)
    log("", lines)

    # ── 确认 schema（模式） ──
    schema_names = ["ozon"]
    log(f"📦 Schema 列表: {schema_names}", lines)
    log("", lines)

    # ── 获取所有表 ──
    tables = await conn.fetch("""
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'ozon'
        ORDER BY table_type, table_name
    """)

    if not tables:
        log("⚠️  未找到任何表，请检查数据库权限或 schema。", lines)
        await conn.close()
        _write_output(lines)
        return

    log("=" * 80, lines)
    log(f"共 {len(tables)} 张表/视图", lines)
    log("=" * 80, lines)

    for row in tables:
        schema = row["table_schema"]
        table = row["table_name"]
        table_type = row["table_type"]

        type_tag = "[VIEW]" if table_type == "VIEW" else "[TABLE]"
        log("", lines)
        log(f"  {type_tag} {table}", lines)

        # ── 获取字段 ──
        columns = await conn.fetch("""
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                c.character_maximum_length,
                tc.constraint_type
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
                ON c.table_schema = kcu.table_schema
                AND c.table_name = kcu.table_name
                AND c.column_name = kcu.column_name
            LEFT JOIN information_schema.table_constraints tc
                ON kcu.constraint_name = tc.constraint_name
                AND kcu.table_schema = tc.table_schema
                AND tc.constraint_type = 'PRIMARY KEY'
            WHERE c.table_schema = $1 AND c.table_name = $2
            ORDER BY c.ordinal_position
        """, schema, table)

        for col in columns:
            pk = " 🔑 PK" if col["constraint_type"] == "PRIMARY KEY" else ""
            nullable = "" if col["is_nullable"] == "YES" else " NOT NULL"
            default = f" DEFAULT {col['column_default']}" if col["column_default"] else ""
            length = f"({col['character_maximum_length']})" if col["character_maximum_length"] else ""
            log(f"      {col['column_name']:<30} {col['data_type']}{length}{nullable}{default}{pk}", lines)

    await conn.close()
    log("", lines)
    log("✅ 探索完成。", lines)

    _write_output(lines)


def _write_output(lines: list[str]) -> None:
    timestamp = datetime.now().strftime("%Y%m%d")
    out_path = OUTPUT_DIR / f"db_ozon_{timestamp}.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📄 结果已保存: {out_path}")


if __name__ == "__main__":
    asyncio.run(explore())
