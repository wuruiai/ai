"""SQLite 连接 + 四条 PRAGMA

数据库连接管理。

Reference: §3.5

四条 PRAGMA 的必要性（按文档 §3.5）：
    - journal_mode=WAL        : 读写不互斥，SSE 长连接期间仍可读
    - foreign_keys=ON         : 连接级！不显式开启则外键形同虚设
    - busy_timeout=5000       : 写锁等待 5 秒，避免短时锁竞争立即失败
    - synchronous=NORMAL      : WAL 模式下的推荐值（OFF/ FULL 都有代价）

资源管理：
    get_connection() 每次返回独立连接；调用方负责 close。
    推荐用 lifespan/上下文管理；本模块不再提供 init_database()，统一入口是
    scripts.init_db → backend.db.migrations.migrate。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from backend.config import settings

_PRAGMAS: list[str] = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
]


async def get_connection() -> aiosqlite.Connection:
    """获取数据库连接（每次独立；调用方负责 close）。"""
    # 确保父目录存在：全新 clone 后 data/ 被 gitignore，若不创建，
    # aiosqlite.connect 会抛 "unable to open database file"，导致
    # scripts.init_db 首次运行必然失败（此前被 chroma 顺带建目录掩盖）。
    Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(settings.SQLITE_PATH)
    for stmt in _PRAGMAS:
        await db.execute(stmt)
    return db


@asynccontextmanager
async def connection_scope() -> AsyncIterator[aiosqlite.Connection]:
    """上下文管理器：自动 close，避免泄漏。"""
    db = await get_connection()
    try:
        yield db
    finally:
        await db.close()


async def close_db(db: aiosqlite.Connection | None) -> None:
    """显式关闭连接；容错处理 None 与已关闭连接。"""
    if db is None:
        return
    try:
        await db.close()
    except Exception:  # noqa: BLE001, S110 -- 已关闭或异常时不抛出（容错关闭）
        pass
