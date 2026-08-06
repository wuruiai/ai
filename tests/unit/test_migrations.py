"""迁移测试：幂等性 + v2 结构。"""

import aiosqlite

from backend.db.migrations import SCHEMA_VERSION, get_schema_version, migrate


async def test_migrate_idempotent(tmp_path):
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        v1 = await get_schema_version(db)
        await migrate(db)  # 第二次应为空操作
        v2 = await get_schema_version(db)
        assert v1 == v2 == SCHEMA_VERSION

        async with db.execute("PRAGMA table_info(users)") as cur:
            users_cols = {r[1] for r in await cur.fetchall()}
        assert {"role", "password_hash", "is_active"} <= users_cols

        async with db.execute("PRAGMA table_info(documents)") as cur:
            doc_cols = {r[1] for r in await cur.fetchall()}
        assert {"user_id", "category", "tags", "is_enabled"} <= doc_cols

        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ) as cur:
            assert await cur.fetchone() is not None  # audit_log 存在
    finally:
        await db.close()
