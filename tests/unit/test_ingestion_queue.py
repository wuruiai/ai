"""摄取任务持久化队列测试（G4.1 / G10.8）。

覆盖：入队幂等（含并发原子）、原子抢占、崩溃恢复（租约/超次数）、
失败重试与终态、运行期租约续期与周期回收。
"""

import asyncio

from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from backend.tasks import queue as q
from backend.tasks.ingestion_worker import IngestionStatus


async def _migrated_db():
    db = await get_connection()
    try:
        await migrate(db)
    finally:
        await close_db(db)


async def _insert_doc(doc_id: str) -> None:
    db = await get_connection()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO documents "
            "(document_id, file_name, stored_path, file_hash, file_size, mime_type, "
            " document_title, status, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                f"{doc_id}.txt",
                f"uploads/{doc_id}.txt",
                doc_id,
                10,
                "text/plain",
                doc_id,
                "pending",
                "u1",
            ),
        )
        await db.commit()
    finally:
        await close_db(db)


async def _task_rows(doc_id: str) -> list[tuple]:
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT task_id, status, attempts, claimed_at, finished_at FROM ingestion_tasks "
            "WHERE document_id=?",
            (doc_id,),
        ) as cur:
            return await cur.fetchall()
    finally:
        await close_db(db)


async def _doc_status(doc_id: str) -> str | None:
    db = await get_connection()
    try:
        async with db.execute("SELECT status FROM documents WHERE document_id=?", (doc_id,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
    finally:
        await close_db(db)


# ---------------------------------------------------------------------------
# enqueue / claim
# ---------------------------------------------------------------------------


async def test_enqueue_idempotent(tmp_path):
    """G4.1：同文档重复入队只有一条非终态任务。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    await q.enqueue("d1")
    await q.enqueue("d1")
    rows = await _task_rows("d1")
    assert len(rows) == 1
    assert rows[0][1] == "pending"


async def test_enqueue_after_failed_allows_retry(tmp_path):
    """G4.1：终态任务重传会新建 pending 任务（失败重试入口）。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    claimed = await q.claim()
    assert claimed is not None
    # 直接终态化
    await q._finalize_failed(claimed, "boom")
    assert await q.enqueue("d1") is True  # 重传：终态后允许新插入
    rows = await _task_rows("d1")
    assert len(rows) == 2
    assert any(r[1] == "pending" for r in rows)


async def test_enqueue_atomic_no_duplicate_on_concurrent(tmp_path):
    """G10.8 M15：并发入队同一文档只产生一条非终态任务（消除 TOCTOU 双插）。

    旧实现"SELECT 查重 → INSERT"两步在并发下可能双插；原子化后
    `INSERT ... SELECT ... WHERE NOT EXISTS` 单语句保证恰好一次插入。
    """
    await _migrated_db()
    await _insert_doc("d1")
    results = await asyncio.gather(*(q.enqueue("d1") for _ in range(5)))
    rows = await _task_rows("d1")
    assert len(rows) == 1
    assert sum(results) == 1  # 仅一次真正插入，其余幂等跳过


async def test_claim_atomic_single_worker(tmp_path):
    """G4.1：claim 抢到唯一 pending，第二次 claim 返回 None（无任务）。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    t1 = await q.claim()
    assert t1 is not None
    assert t1.attempts == 1
    t2 = await q.claim()
    assert t2 is None
    rows = await _task_rows("d1")
    assert rows[0][1] == "running"
    assert rows[0][3] is not None  # claimed_at 已写


async def test_claim_no_duplicate_across_workers(tmp_path):
    """G4.1：并发抢同一任务只有一个成功（乐观锁 status='pending' 守卫）。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")

    # 模拟两个 worker：先都 SELECT 到同一 pending，再各自 UPDATE
    db_a = await get_connection()
    db_b = await get_connection()
    try:
        async with db_a.execute(
            "SELECT task_id FROM ingestion_tasks WHERE status='pending' LIMIT 1"
        ) as cur:
            tid = (await cur.fetchone())[0]
        # A 抢走
        cur = await db_a.execute(
            "UPDATE ingestion_tasks SET status='running', claimed_at=datetime('now'), "
            "attempts=attempts+1 WHERE task_id=? AND status='pending'",
            (tid,),
        )
        assert cur.rowcount == 1
        await db_a.commit()
        # B 抢同一任务 → 0 行
        cur = await db_b.execute(
            "UPDATE ingestion_tasks SET status='running', claimed_at=datetime('now'), "
            "attempts=attempts+1 WHERE task_id=? AND status='pending'",
            (tid,),
        )
        assert cur.rowcount == 0
        await db_b.rollback()
    finally:
        await close_db(db_a)
        await close_db(db_b)
    rows = await _task_rows("d1")
    assert rows[0][2] == 1  # 只有 A 的一次 attempt


# ---------------------------------------------------------------------------
# 恢复
# ---------------------------------------------------------------------------


async def test_recover_stale_running_task(tmp_path):
    """G4.1：超租约的 running 任务回队 pending，可再次 claim。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    claimed = await q.claim()
    assert claimed is not None
    # 把 claimed_at 改到租约之前（模拟 worker 崩溃 10 分钟+）
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE ingestion_tasks SET claimed_at=datetime('now','-900 seconds') WHERE task_id=?",
            (claimed.task_id,),
        )
        await db.commit()
    finally:
        await close_db(db)

    n = await q.recover_stale_tasks()
    assert n == 1
    rows = await _task_rows("d1")
    assert rows[0][1] == "pending"
    assert rows[0][3] is None  # claimed_at 清空

    again = await q.claim()
    assert again is not None
    assert again.attempts == 2


async def test_recover_exhausted_task_fails(tmp_path):
    """G4.1：attempts 已达上限的 running 任务恢复为 failed，文档同步 failed。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    claimed = await q.claim()
    assert claimed is not None
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE ingestion_tasks SET attempts=?, claimed_at=datetime('now','-900 seconds') "
            "WHERE task_id=?",
            (q.MAX_ATTEMPTS, claimed.task_id),
        )
        await db.commit()
    finally:
        await close_db(db)

    n = await q.recover_stale_tasks()
    assert n == 1
    rows = await _task_rows("d1")
    assert rows[0][1] == "failed"
    assert rows[0][4] is not None  # finished_at
    assert await _doc_status("d1") == "failed"


# ---------------------------------------------------------------------------
# worker 执行（monkeypatch ingest_document，验证重试/终态）
# ---------------------------------------------------------------------------


async def test_run_task_success(monkeypatch, tmp_path):
    """G4.1：成功路径 → 文档 ready + chunk_count + 任务 ready。"""
    await _migrated_db()
    await _insert_doc("d1")

    async def _fake_ingest(fp, doc_id, user_id=None):
        db = await get_connection()
        try:
            await db.execute(
                "INSERT INTO chunks (chunk_id, document_id, content, page, chunk_index) "
                "VALUES ('c1', ?, '内容', 1, 0)",
                (doc_id,),
            )
            await db.commit()
        finally:
            await close_db(db)
        return IngestionStatus.READY

    monkeypatch.setattr(q, "ingest_document", _fake_ingest)
    await q.enqueue("d1")
    claimed = await q.claim()
    await q._run_task(claimed)

    assert await _doc_status("d1") == "ready"
    rows = await _task_rows("d1")
    assert rows[0][1] == "ready"
    assert rows[0][4] is not None  # finished_at


async def test_run_task_retries_before_exhausting(monkeypatch, tmp_path):
    """G4.1：失败但未超次数 → 回队 pending 重试。"""
    await _migrated_db()
    await _insert_doc("d1")

    async def _boom(fp, doc_id, user_id=None):
        raise RuntimeError("transient network error")

    monkeypatch.setattr(q, "ingest_document", _boom)
    await q.enqueue("d1")
    claimed = await q.claim()  # attempts=1
    await q._run_task(claimed)

    rows = await _task_rows("d1")
    assert rows[0][1] == "pending"  # 回队
    assert rows[0][2] == 1


async def test_run_task_terminal_after_max_attempts(monkeypatch, tmp_path):
    """G4.1：attempts 达到上限 → 终态 failed + 文档 failed。"""
    await _migrated_db()
    await _insert_doc("d1")

    async def _boom(fp, doc_id, user_id=None):
        raise RuntimeError("persistent failure")

    monkeypatch.setattr(q, "ingest_document", _boom)
    await q.enqueue("d1")
    # 手工把 attempts 顶到上限（第 MAX 次尝试失败即终态）
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE ingestion_tasks SET attempts=? WHERE document_id='d1'",
            (q.MAX_ATTEMPTS,),
        )
        await db.commit()
    finally:
        await close_db(db)

    claimed = await q.claim()  # attempts=MAX_ATTEMPTS
    await q._run_task(claimed)

    rows = await _task_rows("d1")
    assert rows[0][1] == "failed"
    assert await _doc_status("d1") == "failed"


# ---------------------------------------------------------------------------
# G10.8 M16：运行期租约续期 / 周期回收
# ---------------------------------------------------------------------------


async def test_reclaim_stale_running_task_runtime(tmp_path):
    """G10.8 M16：worker 循环内回收超租约 running 任务（进程未重启场景自愈）。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    claimed = await q.claim()
    # 模拟 worker 崩溃但进程未重启：claimed_at 已远超租约
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE ingestion_tasks SET claimed_at=datetime('now','-900 seconds') WHERE task_id=?",
            (claimed.task_id,),
        )
        await db.commit()
    finally:
        await close_db(db)

    n = await q._reclaim_stale_leases()
    assert n == 1
    rows = await _task_rows("d1")
    assert rows[0][1] == "pending"
    assert rows[0][3] is None  # claimed_at 清空，可重新抢占


async def test_reclaim_skips_live_task(tmp_path):
    """G10.8 M16：租约内的 running 任务不被周期回收（长任务不误杀）。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    await q.claim()
    n = await q._reclaim_stale_leases()
    assert n == 0
    rows = await _task_rows("d1")
    assert rows[0][1] == "running"


async def test_lease_renewal_keeps_claimed_at_fresh(monkeypatch, tmp_path):
    """G10.8 M16：运行期续期心跳把 claimed_at 前移，防止长任务被误回收。"""
    await _migrated_db()
    await _insert_doc("d1")
    await q.enqueue("d1")
    claimed = await q.claim()
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE ingestion_tasks SET claimed_at=datetime('now','-5 seconds') WHERE task_id=?",
            (claimed.task_id,),
        )
        await db.commit()
    finally:
        await close_db(db)

    # 用极短续期间隔加速测试（真实值 = LEASE//3，秒级）
    monkeypatch.setattr(q, "RENEW_INTERVAL", 0.05)
    renewal = asyncio.create_task(q._renew_lease(claimed.task_id))
    try:
        await asyncio.sleep(0.15)  # 让心跳跑几次
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)

    db = await get_connection()
    try:
        async with db.execute(
            "SELECT claimed_at >= datetime('now','-2 seconds') "
            "FROM ingestion_tasks WHERE task_id=?",
            (claimed.task_id,),
        ) as cur:
            fresh = (await cur.fetchone())[0]
    finally:
        await close_db(db)
    assert fresh == 1  # claimed_at 已被续期到最近（不再是 -5s 旧值）
