"""SQLite 连接池测试（G4.2）：复用 / 归还 / 事务清理 / 上限 / 关闭。"""

from pathlib import Path

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
