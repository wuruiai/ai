"""schema_version 驱动的版本化迁移

数据库迁移管理。

Reference: §3.7

约定：
    - 版本号单调递增；缺迁移表视为 version 0
    - 每个 vN 包含该版本所需的全部 DDL；版本升级时仅追加
    - 任何 SQL 失败整体回滚
    - 连续两次 migrate() 第二次必须空操作（幂等）
    - 应用启动若发现 schema_version 高于代码支持版本 → 拒绝启动（防止旧代码读新库）
"""

import aiosqlite

from backend.core.logger import get_logger

logger = get_logger(__name__)

# 当前代码支持的最高 schema 版本
SCHEMA_VERSION = 3


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
    current = await get_schema_version(db)

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{current} is newer than supported v{SCHEMA_VERSION}; "
            f"refusing to start to avoid data corruption. "
            f"Please upgrade the application or restore a v{SCHEMA_VERSION} backup."
        )

    if current == SCHEMA_VERSION:
        logger.info("no migration needed.")
        return

    await db.execute("BEGIN")
    try:
        # schema_version 表必须先建，后续所有迁移都依赖它
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                description TEXT,
                applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        if current < 1:
            await _migrate_v1(db)
        if current < 2:
            await _migrate_v2(db)
        if current < 3:
            await _migrate_v3(db)

        # 写版本号（DELETE 旧值保证单行；触发器同步 FTS 也兼容）
        await db.execute("DELETE FROM schema_version")
        await db.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (
                SCHEMA_VERSION,
                "v3: 用户 token_version + refresh_tokens 吊销表（token 可立即失效/登出）",
            ),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    logger.info("migrated to v%d", SCHEMA_VERSION)


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

    # chunks：物理 chunk 存储（id 用 §5.3 稳定规则）
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
