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

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db.connection import close_db, close_pool, get_connection
from backend.db.migrations import migrate


async def _run() -> None:
    print("Initializing database...")
    db = await get_connection()
    try:
        await migrate(db)
    finally:
        # G10.24：统一走 close_db 归还连接（池化时入空闲队列，非池化时关闭），
        # 替代裸 db.close()——后者绕过池的 _all 台账，连接仍被池跟踪为存活；
        # 一次性脚本收尾再关闭连接池，避免 aiosqlite 后台线程拖住解释器不退出
        await close_db(db)
        await close_pool()
    print("Database initialization complete.")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
