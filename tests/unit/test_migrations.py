"""迁移测试：幂等性 + v2 结构 + migration_log 审计 + 降级。"""

import asyncio

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
    """G4.4：降到 v2 —— 版本号回写、v3+ 的表/列移除、日志留痕。"""
    db = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        await migrate(db)
        await downgrade(db, 2)

        assert await get_schema_version(db) == 2
        tables = await _tables(db)
        assert "refresh_tokens" not in tables  # v3
        assert "llm_usage" not in tables  # v4
        assert "audit_log" in tables  # v2 特性应保留

        # v3+ 加的列已删；v2 加的列仍在
        users_cols = await _cols(db, "users")
        assert "token_version" not in users_cols
        assert {"password_hash", "role", "is_active"} <= users_cols
        assert "user_id" in await _cols(db, "documents")
        assert "user_id" in await _cols(db, "messages")
        # v5 队列列已删
        task_cols = await _cols(db, "ingestion_tasks")
        assert "claimed_at" not in task_cols
        assert "attempts" not in task_cols

        # 审计：rolled_back（v5、v4、v3），v2 之后停止
        async with db.execute(
            "SELECT version, name, status FROM migration_log "
            "WHERE status='rolled_back' ORDER BY version"
        ) as cur:
            rows = await cur.fetchall()
        assert [(r[0], r[2]) for r in rows] == [
            (3, "rolled_back"),
            (4, "rolled_back"),
            (5, "rolled_back"),
        ]
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
        task_cols = await _cols(db, "ingestion_tasks")
        assert "claimed_at" not in task_cols
        assert "attempts" not in task_cols

        async with db.execute(
            "SELECT COUNT(*) FROM migration_log WHERE status='rolled_back'"
        ) as cur:
            (n,) = await cur.fetchone()
        assert n == 4  # v2、v3、v4、v5
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
        # applied：首轮 v1..v5(SCHEMA_VERSION) + 回补 v3/v4/v5(3)；rolled_back：v5、v4、v3
        assert counts["applied"] == SCHEMA_VERSION + 3
        assert counts["rolled_back"] == 3
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


# ---------------------------------------------------------------------------
# G10.24：并发实例启动时 migrate 必须串行化
# ---------------------------------------------------------------------------


class _PauseResult:
    """复刻 aiosqlite `execute` 的双协议（awaitable + async ctx mgr）。

    首个连接读 schema 版本时让出 50ms——修复代码里版本读在 BEGIN IMMEDIATE
    写锁内，让出期间第二个并发 migrate 的 BEGIN IMMEDIATE 在 SQLite 层排队；
    未修复代码里版本读在事务外，两个 migrate 都能读到旧版本、双双执行非幂等
    ALTER，产生确定性分歧。"""

    def __init__(self, inner, sql, args, kwargs, pause):
        self._inner = inner
        self._sql = sql
        self._args = args
        self._kwargs = kwargs
        self._pause = pause
        self._ctx = None

    async def _start(self):
        self._ctx = self._inner.execute(self._sql, *self._args, **self._kwargs)
        cur = await self._ctx.__aenter__()
        if self._pause and "SELECT version FROM schema_version" in self._sql:
            await asyncio.sleep(0.05)
        return cur

    def __await__(self):
        return self._start().__await__()

    async def __aenter__(self):
        return await self._start()

    async def __aexit__(self, *exc):
        if self._ctx is not None:
            return await self._ctx.__aexit__(*exc)
        return False


class _ConnProxy:
    def __init__(self, inner, pause):
        self._inner = inner
        self._pause = pause

    def execute(self, sql, *args, **kwargs):
        return _PauseResult(self._inner, sql, args, kwargs, self._pause)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_concurrent_migrate_is_serialized(tmp_path):
    """G10.24：两个并发 migrate 启动时串行化——后到者等首个提交后读到新版本
    空操作，绝不重复执行非幂等 ALTER TABLE ADD COLUMN。

    未修复时版本检查在事务外：两个 migrate 都读到 v0 并同时执行 _migrate_v2 的
    `ALTER TABLE users ADD COLUMN role`，先到者提交后后到者在该列上报
    duplicate column。修复后 BEGIN IMMEDIATE 把首个的整个迁移（含版本读）锁进
    写锁，第二个阻塞至提交后才读版本 → 直接空操作。
    """
    db_a = await aiosqlite.connect(str(tmp_path / "m.db"))
    db_b = await aiosqlite.connect(str(tmp_path / "m.db"))
    try:
        results = await asyncio.gather(
            migrate(_ConnProxy(db_a, pause=True)),
            migrate(_ConnProxy(db_b, pause=False)),
        )
        # 两个实例都正常返回（无 duplicate column / database is locked）
        assert results == [None, None]
        assert await get_schema_version(db_a) == SCHEMA_VERSION
        # migration_log 只写一轮，未被并发重复
        async with db_a.execute("SELECT COUNT(*) FROM migration_log") as cur:
            (n,) = await cur.fetchone()
        assert n == SCHEMA_VERSION
    finally:
        await db_a.close()
        await db_b.close()
