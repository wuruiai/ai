"""数据库幂等迁移脚本

初始化 SQLite 数据库，执行 schema 迁移。

幂等性保证：
    - 建表全部使用 `CREATE TABLE IF NOT EXISTS`，可重复执行
    - schema_version 表记录当前版本号；版本一致时跳过 migrate
    - seed 默认用户使用 `INSERT OR IGNORE`，不会重复插入

Usage:
    python -m scripts.init_db
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db.connection import get_connection
from backend.db.migrations import migrate


async def _run() -> None:
    print("Initializing database...")
    db = await get_connection()
    try:
        await migrate(db)
    finally:
        await db.close()
    print("Database initialization complete.")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
