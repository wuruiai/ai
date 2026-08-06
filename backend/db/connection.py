"""SQLite 连接池 + 四条 PRAGMA（G4.2）

数据库连接管理。


四条 PRAGMA 的必要性：
    - journal_mode=WAL        : 读写不互斥，SSE 长连接期间仍可读
    - foreign_keys=ON         : 连接级！不显式开启则外键形同虚设
    - busy_timeout=5000       : 写锁等待 5 秒，避免短时锁竞争立即失败
    - synchronous=NORMAL      : WAL 模式下的推荐值（OFF/ FULL 都有代价）

连接池（G4.2）：
    - `get_connection()` / `close_db()` 签名不变，内部改为有界连接池 checkout/checkin，
      调用方零改动；PRAGMA 只在建连时设置一次。
    - `DB_POOL_SIZE <= 1` 时不池化：每次新建、调用方关闭（测试 conftest 强制 0，
      保证 `_fresh_db` 删库重建后不会拿到指向旧文件的复用连接）。

资源管理：
    推荐用 lifespan/上下文管理；本模块不再提供 init_database()，统一入口是
    scripts.init_db → backend.db.migrations.migrate。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from backend.config import settings

_PRAGMAS: list[str] = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
]


class SQLitePool:
    """有界 SQLite 连接池（asyncio）。

    - `acquire()`: 有空闲连接则复用，否则新建（池满/空池不阻塞，直接新建）
    - `release()`: 回滚遗留事务后归还空闲队列；队列满则直接关闭（控制闲置上限）
    - `size <= 1`: 完全禁用池化，acquire 每次新建、release 直接关闭
    """

    def __init__(self, size: int, path: str) -> None:
        self._size = size
        self._path = path
        # 仅在启用池化时创建队列；避免测试（size=0）在 import 期绑定事件循环
        self._idle: asyncio.Queue[aiosqlite.Connection] | None = (
            asyncio.Queue(maxsize=size) if size > 1 else None
        )
        # 记录池创建过的全部连接，供 close() 在应用关闭时兜底清理，
        # 避免 aiosqlite 后台线程（非 daemon）拖住解释器不退出
        self._all: set[aiosqlite.Connection] = set()

    async def _connect(self) -> aiosqlite.Connection:
        # 确保父目录存在：全新 clone 后 data/ 被 gitignore，若不创建，
        # aiosqlite.connect 会抛 "unable to open database file"。
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._path)
        for stmt in _PRAGMAS:
            await db.execute(stmt)
        self._all.add(db)
        return db

    async def close(self) -> None:
        """关闭池持有的全部连接（应用关闭/测试清理时调用）。

        仅应在事件循环即将结束时调用：会连仍在使用的连接一起关掉。
        """
        if self._idle is not None:
            while not self._idle.empty():
                try:
                    await self._idle.get_nowait().close()
                except Exception:  # noqa: BLE001, S110 -- 关闭失败忽略
                    pass
        for db in list(self._all):
            try:
                await db.close()
            except Exception:  # noqa: BLE001, S110 -- 已关闭/异常忽略
                pass
        self._all.clear()

    async def acquire(self) -> aiosqlite.Connection:
        """从池取出连接（池化开启时）；否则新建。"""
        if self._size <= 1 or self._idle is None:
            return await self._connect()
        if not self._idle.empty():
            return self._idle.get_nowait()
        return await self._connect()

    async def release(self, db: aiosqlite.Connection) -> None:
        """归还连接（池化开启时）；否则直接关闭。"""
        if self._size <= 1 or self._idle is None:
            await db.close()
            return
        # 清掉可能遗留的未提交事务，避免脏状态污染下次复用；
        # 回滚失败说明连接已坏，直接丢弃
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 -- 连接损坏，丢弃
            await db.close()
            return
        try:
            self._idle.put_nowait(db)
        except asyncio.QueueFull:
            await db.close()


# 模块级单例（路径/大小来自 settings；测试中 DB_POOL_SIZE=0 → 不池化）
_db_pool = SQLitePool(settings.DB_POOL_SIZE, settings.SQLITE_PATH)


async def get_connection() -> aiosqlite.Connection:
    """获取数据库连接（从池 checkout；调用方负责 close_db 归还）。"""
    return await _db_pool.acquire()


async def close_db(db: aiosqlite.Connection | None) -> None:
    """归还/关闭连接（checkin）；容错处理 None 与已关闭连接。"""
    if db is None:
        return
    try:
        await _db_pool.release(db)
    except Exception:  # noqa: BLE001, S110 -- 已关闭或异常时不抛出（容错归还）
        pass
