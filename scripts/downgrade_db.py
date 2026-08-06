"""数据库降级脚本（G4.4）

把 schema 从当前版本降到目标版本（默认 SCHEMA_VERSION - 1），用于发布回滚。

与 migrate() 同源、同事务语义：
    - 每一步降级（DROP TABLE / DROP COLUMN）成功后写入 migration_log（status='rolled_back'）
    - 任一步失败整体回滚
    - 不支持降到 v1 以下（会删除全部基础表，属破坏性操作）

Usage:
    python -m scripts.downgrade_db                # 降到 SCHEMA_VERSION-1
    python -m scripts.downgrade_db --to 2         # 直接降到 v2
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db.connection import get_connection
from backend.db.migrations import SCHEMA_VERSION, downgrade, get_schema_version


async def _run(to_version: int) -> None:
    db = await get_connection()
    try:
        current = await get_schema_version(db)
        if to_version >= current:
            print(f"Nothing to downgrade (current=v{current}, to=v{to_version}).")
            return
        await downgrade(db, to_version)
        print(f"Downgraded v{current} -> v{to_version}.")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Downgrade the database schema")
    parser.add_argument(
        "--to",
        type=int,
        default=SCHEMA_VERSION - 1,
        help=f"target schema version (default: {SCHEMA_VERSION - 1})",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.to))


if __name__ == "__main__":
    main()
