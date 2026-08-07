"""SQLite 连接池测试（G4.2）：复用 / 归还 / 事务清理 / 上限 / 关闭。"""

import sqlite3
from pathlib import Path

import pytest

from backend.db.connection import SQLitePool, close_db, get_connection


async def _with_pool(size: int, path: Path, fn):
    pool = SQLitePool(size=size, path=str(path))
    try:
        await fn(pool)
    finally:
        # 关掉池持有的全部连接，避免 aiosqlite 后台线程拖住测试进程退出
        await pool.close()


async def test_acquire_release_reuses_connection(tmp_path: Path):
    async def run(pool):
        a = await pool.acquire()
        await pool.release(a)
        b = await pool.acquire()
        assert b is a  # 归还后复用同一连接
        await pool.release(b)

    await _with_pool(3, tmp_path / "pool.db", run)


async def test_pool_bounded_by_size(tmp_path: Path):
    async def run(pool):
        c1, c2 = await pool.acquire(), await pool.acquire()
        ids = {id(c1), id(c2)}
        await pool.release(c1)
        await pool.release(c2)
        # 连续取两次都来自空闲池，不再新建
        d1, d2 = await pool.acquire(), await pool.acquire()
        assert {id(d1), id(d2)} == ids
        await pool.release(d1)
        await pool.release(d2)

    await _with_pool(2, tmp_path / "pool.db", run)


async def test_release_rolls_back_open_transaction(tmp_path: Path):
    async def run(pool):
        db = await pool.acquire()
        await db.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO t VALUES (1)")  # 未 commit，留下打开的事务
        await pool.release(db)
        # 复用时事务已被回滚：INSERT 不可见
        db2 = await pool.acquire()
        async with db2.execute("SELECT COUNT(*) FROM t") as cur:
            n = (await cur.fetchone())[0]
        assert n == 0
        await pool.release(db2)

    await _with_pool(2, tmp_path / "pool.db", run)


async def test_size_one_disables_pooling(tmp_path: Path):
    async def run(pool):
        a = await pool.acquire()
        await pool.release(a)
        b = await pool.acquire()
        assert b is not a  # 每次新建
        await pool.release(b)

    await _with_pool(1, tmp_path / "pool.db", run)


async def test_get_connection_close_db_roundtrip(tmp_path: Path, monkeypatch):
    import backend.db.connection as conn_mod

    async def run(pool):
        monkeypatch.setattr(conn_mod, "_db_pool", pool)
        db = await get_connection()
        await db.execute("CREATE TABLE IF NOT EXISTS x (v INTEGER)")
        await close_db(db)
        db2 = await get_connection()
        assert db2 is db  # 公共 API 复用了同一连接
        await close_db(db2)

    await _with_pool(3, tmp_path / "api.db", run)


async def test_closed_db_is_tolerated(tmp_path: Path):
    async def run(pool):
        db = await pool.acquire()
        await db.close()
        await pool.release(db)  # 不应抛异常

    await _with_pool(1, tmp_path / "pool.db", run)


async def test_close_drains_idle_connections(tmp_path: Path):
    """close() 应关掉空闲队列里仍打开的连接。"""
    pool = SQLitePool(size=3, path=str(tmp_path / "pool.db"))
    c1 = await pool.acquire()
    await pool.release(c1)  # 进入空闲队列
    await pool.close()  # 不应挂起、不应抛异常


async def test_release_discards_closed_connection_from_all(tmp_path: Path):
    """G10.24：连接被 release 关闭后应从 _all 摘除，避免集合只增不减。

    非池化（size<=1）路径 release 即关闭连接，此前从不 discard —— 反复
    acquire/release 后 _all 无限增长，close() 要逐一（重）关闭已死连接。
    """

    async def run(pool):
        for _ in range(3):
            db = await pool.acquire()
            assert len(pool._all) == 1
            await pool.release(db)
            assert len(pool._all) == 0  # 已关闭，不再被池跟踪

    await _with_pool(1, tmp_path / "pool.db", run)


async def test_release_keeps_live_connection_in_all(tmp_path: Path):
    """G10.24：池化路径归还到空闲队列的连接仍存活，应保留在 _all（供 close 兜底）。"""
    pool = SQLitePool(size=2, path=str(tmp_path / "pool2.db"))
    a = await pool.acquire()
    await pool.release(a)
    assert len(pool._all) == 1
    await pool.close()
    assert len(pool._all) == 0


async def test_connect_pragma_failure_does_not_leak(tmp_path: Path, monkeypatch):
    """G10.24：建连 PRAGMA 失败时连接被关闭并从 _all 摘除，不泄漏后台线程。

    注：`PRAGMA bogus_option = 1` 在 Python sqlite3 里静默 no-op 不抛错，
    故用一条必然失败的语句 `NOT VALID SQL` 触发建连清理路径——与真实场景
    （WAL/外键 PRAGMA 在只读目录等环境下失败）走同一段 except。
    """
    import backend.db.connection as conn_mod

    monkeypatch.setattr(conn_mod, "_PRAGMAS", ["NOT VALID SQL"])
    pool = SQLitePool(size=0, path=str(tmp_path / "pool.db"))
    with pytest.raises(sqlite3.OperationalError):
        await pool.acquire()
    assert len(pool._all) == 0  # 失败连接已关闭并摘除
    await pool.close()


async def test_close_pool_releases_module_pool(tmp_path: Path, monkeypatch):
    """G10.24：close_pool 关闭模块级池全部连接——一次性脚本退出前防解释器挂起。

    池化下 close_db 只是归还空闲队列（连接仍存活、线程仍在），脚本退出前必须
    显式关闭整个池（同 main.py lifespan 的 _db_pool.close()）。
    """
    import backend.db.connection as conn_mod

    pool = SQLitePool(size=2, path=str(tmp_path / "pool.db"))
    monkeypatch.setattr(conn_mod, "_db_pool", pool)
    db = await conn_mod.get_connection()
    await conn_mod.close_db(db)  # 池化下归还空闲队列，连接仍存活
    assert len(pool._all) == 1
    await conn_mod.close_pool()
    assert len(pool._all) == 0  # 全部关闭，进程可干净退出
