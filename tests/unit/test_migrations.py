"""迁移测试：幂等性 + v2 结构 + migration_log 审计 + 降级。"""

import aiosqlite

from backend.db.migrations import (
    SCHEMA_VERSION,
    downgrade,
    get_schema_version,
    migrate,
)


async def _cols(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return {r[1] for r in await cur.fetchall()}


async def _tables(db: aiosqlite.Connection) -> set[str]:
    async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        return {r[0] for r in await cur.fetchall()}


async def test_migrate_idempotent(tmp_path):
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        v1 = await get_schema_version(db)
        await migrate(db)  # 第二次应为空操作
        v2 = await get_schema_version(db)
        assert v1 == v2 == SCHEMA_VERSION

        users_cols = await _cols(db, "users")
        assert {"role", "password_hash", "is_active", "token_version"} <= users_cols

        doc_cols = await _cols(db, "documents")
        assert {"user_id", "category", "tags", "is_enabled"} <= doc_cols

        assert "audit_log" in await _tables(db)
    finally:
        await db.close()


async def test_migrate_writes_migration_log(tmp_path):
    """G4.4：migrate 后 migration_log 记录 v1..v4 且 status='applied'。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        async with db.execute(
            "SELECT version, name, status FROM migration_log ORDER BY version"
        ) as cur:
            rows = await cur.fetchall()
        assert [(r[0], r[2]) for r in rows] == [
            (v, "applied") for v in range(1, SCHEMA_VERSION + 1)
        ]
    finally:
        await db.close()


async def test_migrate_idempotent_does_not_dup_log(tmp_path):
    """G4.4：重复 migrate 不重复写日志（第二次为空操作）。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        await migrate(db)
        async with db.execute("SELECT COUNT(*) FROM migration_log") as cur:
            (n,) = await cur.fetchone()
        assert n == SCHEMA_VERSION
    finally:
        await db.close()


async def test_downgrade_to_v2(tmp_path):
    """G4.4：降到 v2 —— 版本号回写、v3/v4 的表/列移除、日志留痕。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        await downgrade(db, 2)

        assert await get_schema_version(db) == 2
        tables = await _tables(db)
        assert "refresh_tokens" not in tables  # v3
        assert "llm_usage" not in tables  # v4
        assert "audit_log" in tables  # v2 特性应保留

        # v3/v4 加的列已删；v2 加的列仍在
        users_cols = await _cols(db, "users")
        assert "token_version" not in users_cols
        assert {"password_hash", "role", "is_active"} <= users_cols
        assert "user_id" in await _cols(db, "documents")
        assert "user_id" in await _cols(db, "messages")

        # 审计：两条 rolled_back（v4、v3），v2 之后停止
        async with db.execute(
            "SELECT version, name, status FROM migration_log "
            "WHERE status='rolled_back' ORDER BY version"
        ) as cur:
            rows = await cur.fetchall()
        assert [(r[0], r[2]) for r in rows] == [(3, "rolled_back"), (4, "rolled_back")]
    finally:
        await db.close()


async def test_downgrade_to_v1(tmp_path):
    """G4.4：降到 v1 —— 覆盖 _downgrade_v2（删 audit_log 与 RBAC 列）。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        await downgrade(db, 1)

        assert await get_schema_version(db) == 1
        tables = await _tables(db)
        assert "audit_log" not in tables
        assert "refresh_tokens" not in tables
        assert "llm_usage" not in tables

        users_cols = await _cols(db, "users")
        assert not {"password_hash", "role", "is_active", "token_version"} & users_cols
        assert "user_id" not in await _cols(db, "documents")
        assert "user_id" not in await _cols(db, "messages")

        async with db.execute(
            "SELECT COUNT(*) FROM migration_log WHERE status='rolled_back'"
        ) as cur:
            (n,) = await cur.fetchone()
        assert n == 3  # v2、v3、v4
    finally:
        await db.close()


async def test_downgrade_then_remigrate(tmp_path):
    """G4.4：降级后可再次升级回 SCHEMA_VERSION，日志只增不减。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        await downgrade(db, 2)
        await migrate(db)
        assert await get_schema_version(db) == SCHEMA_VERSION
        assert "llm_usage" in await _tables(db)
        assert "refresh_tokens" in await _tables(db)
        assert "audit_log" in await _tables(db)
        assert "token_version" in await _cols(db, "users")

        async with db.execute("SELECT status, COUNT(*) FROM migration_log GROUP BY status") as cur:
            counts = dict(await cur.fetchall())
        # applied：首轮 v1..v4(4) + 回补 v3/v4(2)；rolled_back：v4、v3
        assert counts["applied"] == SCHEMA_VERSION + 2
        assert counts["rolled_back"] == 2
    finally:
        await db.close()


async def test_downgrade_too_high_is_noop(tmp_path):
    """G4.4：to_version >= 当前版本时为空操作。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        await downgrade(db, SCHEMA_VERSION + 1)
        assert await get_schema_version(db) == SCHEMA_VERSION
        assert "llm_usage" in await _tables(db)
    finally:
        await db.close()
