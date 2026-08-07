"""schema_version 驱动的版本化迁移

数据库迁移管理。


约定：
    - 版本号单调递增；缺迁移表视为 version 0
    - 每个 vN 包含该版本所需的全部 DDL；版本升级时仅追加
    - 任何 SQL 失败整体回滚
    - 连续两次 migrate() 第二次必须空操作（幂等）
    - 应用启动若发现 schema_version 高于代码支持版本 → 拒绝启动（防止旧代码读新库）
    - 每次应用的迁移写入 migration_log（G4.4 审计）；SQLite DDL 本身是事务性的，
      因此 migrate()/downgrade() 都能整体回滚
    - 降级 downgrade() 在 SQLite 能力范围内执行（删表 + DROP COLUMN，SQLite ≥3.35）
"""

from collections.abc import Awaitable, Callable

import aiosqlite

from backend.core.logger import get_logger

logger = get_logger(__name__)

# 当前代码支持的最高 schema 版本
SCHEMA_VERSION = 5


async def _ensure_framework_tables(db: aiosqlite.Connection) -> None:
    """保证框架自持表存在：schema_version + migration_log（G4.4）。"""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            version    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'applied'
                       CHECK (status IN ('applied','rolled_back')),
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


async def get_schema_version(db: aiosqlite.Connection) -> int:
    """获取当前 schema 版本。"""
    try:
        async with db.execute("SELECT version FROM schema_version LIMIT 1") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001 -- 未初始化/损坏时按 0 处理，交由 migrate() 全量重建
        return 0


async def migrate(db: aiosqlite.Connection) -> None:
    """读取当前版本 → 应用未执行版本 → 写入新版本。

    单事务；任一语句失败整体回滚（SQLite 默认每条语句独立事务，
    我们用 explicit BEGIN/COMMIT 包住）。
    """
    # G10.24：BEGIN IMMEDIATE 先取写锁再读版本，串行化并发实例启动时的迁移。
    # 此前版本检查在事务外：两个实例同时启动都读到旧版本、同时执行非幂等的
    # ALTER TABLE ADD COLUMN（_migrate_v2/_v3），后到者报 duplicate column。
    await db.execute("BEGIN IMMEDIATE")
    try:
        current = await get_schema_version(db)

        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{current} is newer than supported v{SCHEMA_VERSION}; "
                f"refusing to start to avoid data corruption. "
                f"Please upgrade the application or restore a v{SCHEMA_VERSION} backup."
            )

        if current == SCHEMA_VERSION:
            await db.commit()  # 空操作也要提交，结束 BEGIN IMMEDIATE 拿到的写锁
            logger.info("no migration needed.")
            return

        # 框架表（schema_version + migration_log）先建，后续迁移依赖它
        await _ensure_framework_tables(db)

        applied: list[int] = []
        if current < 1:
            await _migrate_v1(db)
            applied.append(1)
        if current < 2:
            await _migrate_v2(db)
            applied.append(2)
        if current < 3:
            await _migrate_v3(db)
            applied.append(3)
        if current < 4:
            await _migrate_v4(db)
            applied.append(4)
        if current < 5:
            await _migrate_v5(db)
            applied.append(5)

        # 写版本号（DELETE 旧值保证单行；触发器同步 FTS 也兼容）
        await db.execute("DELETE FROM schema_version")
        await db.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (
                SCHEMA_VERSION,
                "v5: ingestion_tasks 持久化队列列（G4.1 claimed_at/attempts）",
            ),
        )
        # G4.4：每次实际应用的迁移写入审计日志（与版本号同一事务，回滚则一并消失）
        for v in applied:
            await db.execute(
                "INSERT INTO migration_log (version, name) VALUES (?, ?)",
                (v, f"_migrate_v{v}"),
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    logger.info("migrated to v%d", SCHEMA_VERSION)


async def downgrade(db: aiosqlite.Connection, to_version: int) -> None:
    """把 schema 从当前版本降到 to_version（SQLite 能力范围内）。

    - to_version >= 当前版本时为空操作
    - 逐步执行 _downgrade_vN，删除高于目标版本的迁移
    - 与 migrate() 同事务：任一步失败整体回滚
    """
    # G10.24：与 migrate() 一致，先取写锁再读版本，避免与并发升级/另一进程降级交错
    await db.execute("BEGIN IMMEDIATE")
    try:
        current = await get_schema_version(db)
        if to_version >= current:
            await db.commit()  # 空操作也要提交，结束写锁
            logger.info("nothing to downgrade (current=%d, to=%d)", current, to_version)
            return
        if to_version < 0:
            raise ValueError("to_version must be >= 0")

        await _ensure_framework_tables(db)
        for v in range(current, to_version, -1):
            await _apply_downgrade(db, v)
        await db.execute("DELETE FROM schema_version")
        await db.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (to_version, f"downgraded from v{current}"),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    logger.info("downgraded to v%d", to_version)


async def _apply_downgrade(db: aiosqlite.Connection, version: int) -> None:
    """执行单步降级并写 migration_log（status='rolled_back'）。"""
    handler = _DOWNGRADES.get(version)
    if handler is None:
        raise RuntimeError(f"no downgrade handler for v{version}")
    await handler(db)
    await db.execute(
        "INSERT INTO migration_log (version, name, status) VALUES (?, ?, 'rolled_back')",
        (version, f"_downgrade_v{version}"),
    )


# ---------------------------------------------------------------------------
# v1: 初始结构
# ---------------------------------------------------------------------------


async def _migrate_v1(db: aiosqlite.Connection) -> None:
    # users：本机单用户，固定 local_user
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id           TEXT PRIMARY KEY,
            username     TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        "INSERT OR IGNORE INTO users (id, username, display_name) VALUES (?, ?, ?)",
        ("local_user", "local", "本机用户"),
    )

    # documents：上传文档元数据；file_hash UNIQUE 用于重复导入识别
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id    TEXT PRIMARY KEY,
            file_name      TEXT    NOT NULL,
            stored_path    TEXT    NOT NULL,
            file_hash      TEXT    NOT NULL UNIQUE,
            file_size      INTEGER NOT NULL,
            mime_type      TEXT,
            document_title TEXT    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','parsing','chunking','embedding',
                                             'indexing','ready','failed')),
            error_msg      TEXT,
            chunk_count    INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")

    # chunks：物理 chunk 存储（id 用内容哈希派生，重传不产生重复 chunk）
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id    TEXT PRIMARY KEY,
            document_id TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            page        INTEGER,
            chunk_index INTEGER NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")

    # FTS5 virtual table: external content 模式与 chunks 同步（禁止应用层双写）
    # tokenize='trigram'：中文场景必须用 trigram（unicode61 不切分中文，
    # 会导致 "水利工程" 等中文查询 0 命中）。见准备文档附录 B。
    await db.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content,
            content='chunks',
            content_rowid='rowid',
            tokenize='trigram'
        )
        """
    )
    # 触发器：chunks 增/改/删时同步 FTS
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
        END
        """
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
        END
        """
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
            INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
        END
        """
    )

    # messages：问答消息；message_id 闭环核心
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            thread_id  TEXT NOT NULL,
            role       TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
            content    TEXT NOT NULL,
            citations_json TEXT,
            agent_type TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at)"
    )

    # feedback：反馈接口；外键链 feedback → messages
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            feedback_id TEXT PRIMARY KEY,
            message_id  TEXT NOT NULL,
            rating      TEXT NOT NULL CHECK (rating IN ('helpful','not_helpful')),
            comment     TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id)")

    # ingestion_tasks：摄取任务表（CAP-P0-01 任务追踪）
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_tasks (
            task_id     TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','parsing','chunking','embedding',
                                          'indexing','ready','failed')),
            error_msg   TEXT,
            started_at  TEXT,
            finished_at TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_doc ON ingestion_tasks(document_id)")

    logger.info("seed local_user ok")


# ---------------------------------------------------------------------------
# v2: 企业级 —— 多用户 RBAC + 数据归属 + 审计日志 + 知识库结构化
# ---------------------------------------------------------------------------


async def _migrate_v2(db: aiosqlite.Connection) -> None:
    # users：加密码 / 角色 / 状态；现有 local 本机用户升为管理员
    await db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    await db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    await db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    # 注意：SQLite ALTER TABLE ADD COLUMN 的默认值必须是常量，不能是 datetime('now')，
    # 故不在此加 updated_at 列
    # 不把 v1 的 local 种子升为 admin：企业级语义下"首个注册用户成为管理员"，
    # 而 local 无密码不可登录（注册接口统计 admin 时也会排除 local）。

    # documents：归属用户 + 知识库结构化（分类/标签/启用开关）
    await db.execute("ALTER TABLE documents ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local_user'")
    await db.execute("ALTER TABLE documents ADD COLUMN category TEXT")
    await db.execute("ALTER TABLE documents ADD COLUMN tags TEXT")
    await db.execute("ALTER TABLE documents ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id)")

    # messages：归属用户（thread 级隔离）
    await db.execute("ALTER TABLE messages ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local_user'")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, thread_id)")

    # audit_log：审计留痕
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id      TEXT PRIMARY KEY,
            user_id     TEXT,
            username    TEXT,
            action      TEXT    NOT NULL,
            target_type TEXT,
            target_id   TEXT,
            detail      TEXT,
            ip          TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, created_at)")

    logger.info("migrate v2 ok (RBAC + 数据归属 + 审计 + 知识库结构化)")


# ---------------------------------------------------------------------------
# v3: Token 安全 —— token_version 立即失效 + refresh token 吊销
# ---------------------------------------------------------------------------


async def _migrate_v3(db: aiosqlite.Connection) -> None:
    # users.token_version：密码修改/权限变更时 bump，签发过的旧 token 立即失效
    await db.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")

    # refresh_tokens：长期 refresh token 落库（可吊销），支持登出/轮换
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token_id   TEXT PRIMARY KEY,
            user_id    TEXT    NOT NULL,
            expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            created_at TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id)")

    logger.info("migrate v3 ok (token_version + refresh_tokens)")


# ---------------------------------------------------------------------------
# v4: LLM 用量 / 成本记账（G3.1）
# ---------------------------------------------------------------------------


async def _migrate_v4(db: aiosqlite.Connection) -> None:
    # llm_usage：每轮 LLM 调用一行，只插入不更新（append-only 便于审计与聚合）
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage (
            usage_id      TEXT    PRIMARY KEY,
            user_id       TEXT    NOT NULL DEFAULT 'local_user',
            agent_type    TEXT    NOT NULL DEFAULT 'knowledge_qa',
            model         TEXT    NOT NULL,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_cny      REAL    NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_user ON llm_usage(user_id, created_at)"
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at)")

    logger.info("migrate v4 ok (llm_usage)")


async def _migrate_v5(db: aiosqlite.Connection) -> None:
    # 持久化队列（G4.1）：重建 ingestion_tasks 表——
    #   status CHECK 加入 'running'（worker 抢占中状态，旧 CHECK 没有）
    #   新增 claimed_at（租约时间）/ attempts（尝试次数）
    # SQLite 无法 ALTER CHECK，用标准的"建新表→拷贝→删旧→改名"12 步法。
    await db.execute(
        """
        CREATE TABLE ingestion_tasks_v5 (
            task_id     TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','parsing','chunking',
                                          'embedding','indexing','ready','failed')),
            error_msg   TEXT,
            claimed_at  TEXT,
            attempts    INTEGER NOT NULL DEFAULT 0,
            started_at  TEXT,
            finished_at TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        """
        INSERT INTO ingestion_tasks_v5
            (task_id, document_id, status, error_msg, started_at, finished_at, created_at)
        SELECT task_id, document_id, status, error_msg, started_at, finished_at, created_at
        FROM ingestion_tasks
        """
    )
    await db.execute("DROP TABLE ingestion_tasks")
    await db.execute("ALTER TABLE ingestion_tasks_v5 RENAME TO ingestion_tasks")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_doc ON ingestion_tasks(document_id)")

    logger.info("migrate v5 ok (ingestion_tasks queue: running status + claimed_at/attempts)")


# ---------------------------------------------------------------------------
# 降级（G4.4）—— 与升级同源，SQLite DROP COLUMN / DROP TABLE 均为事务性 DDL
# ---------------------------------------------------------------------------


async def _downgrade_v5(db: aiosqlite.Connection) -> None:
    """v5 → v4：重建 ingestion_tasks 回到 v1 结构（无 running/claimed_at/attempts）。"""
    await db.execute(
        """
        CREATE TABLE ingestion_tasks_v4 (
            task_id     TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','parsing','chunking','embedding',
                                          'indexing','ready','failed')),
            error_msg   TEXT,
            started_at  TEXT,
            finished_at TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
        )
        """
    )
    await db.execute(
        """
        INSERT INTO ingestion_tasks_v4
            (task_id, document_id, status, error_msg, started_at, finished_at, created_at)
        SELECT task_id, document_id, status, error_msg, started_at, finished_at, created_at
        FROM ingestion_tasks
        """
    )
    await db.execute("DROP TABLE ingestion_tasks")
    await db.execute("ALTER TABLE ingestion_tasks_v4 RENAME TO ingestion_tasks")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_doc ON ingestion_tasks(document_id)")


async def _downgrade_v2(db: aiosqlite.Connection) -> None:
    """v2 → v1：移除 RBAC / 数据归属 / 审计日志 / 知识库结构化列。"""
    # SQLite DROP COLUMN 不允许列上有索引，先删索引再删列
    await db.execute("DROP INDEX IF EXISTS idx_documents_user")
    await db.execute("DROP INDEX IF EXISTS idx_messages_user")
    await db.execute("ALTER TABLE users DROP COLUMN password_hash")
    await db.execute("ALTER TABLE users DROP COLUMN role")
    await db.execute("ALTER TABLE users DROP COLUMN is_active")
    await db.execute("ALTER TABLE documents DROP COLUMN user_id")
    await db.execute("ALTER TABLE documents DROP COLUMN category")
    await db.execute("ALTER TABLE documents DROP COLUMN tags")
    await db.execute("ALTER TABLE documents DROP COLUMN is_enabled")
    await db.execute("ALTER TABLE messages DROP COLUMN user_id")
    await db.execute("DROP TABLE IF EXISTS audit_log")


async def _downgrade_v3(db: aiosqlite.Connection) -> None:
    """v3 → v2：移除 token_version 与 refresh_tokens。"""
    await db.execute("ALTER TABLE users DROP COLUMN token_version")
    await db.execute("DROP TABLE IF EXISTS refresh_tokens")


async def _downgrade_v4(db: aiosqlite.Connection) -> None:
    """v4 → v3：移除 llm_usage（append-only 记账表）。"""
    await db.execute("DROP TABLE IF EXISTS llm_usage")


# 降级处理器注册表：version -> handler。
# 无 v1：降级到 v1 需删除全部基础表（users/documents/chunks/...），属破坏性操作，不支持。
_DOWNGRADES: dict[int, Callable[[aiosqlite.Connection], Awaitable[None]]] = {
    5: _downgrade_v5,
    4: _downgrade_v4,
    3: _downgrade_v3,
    2: _downgrade_v2,
}
