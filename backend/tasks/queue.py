"""摄取任务持久化队列（G4.1）

把"上传即跑"的进程内 asyncio.create_task 改为 SQLite 持久化队列 + worker：

    enqueue(document_id)    写入 ingestion_tasks(status='pending')，幂等且原子（M15）
    claim()                 原子抢占一个 pending 任务 → 'running'（attempts+1）
    worker_loop()           claim → ingest_document → 落最终态；失败可重试或标 failed
    recover_stale_tasks()   启动时回收超租约的 running 任务（回队/超次终态）
    _renew_lease()          运行期租约续期（心跳），长任务不被周期回收误判重抢（M16）
    _reclaim_stale_leases() worker 循环内周期性回收超租约任务（进程未重启也自愈，M16）

多 worker（应用进程内 + 独立 scripts.worker 进程）共享同一 SQLite 队列，
靠 claim 的原子 UPDATE 互斥，同一任务只会被一个 worker 抢占。

状态流：
    pending → running → ready
               │  失败且 attempts < MAX_ATTEMPTS → pending（重试）
               └  失败且 attempts >= MAX_ATTEMPTS / 文档被删 → failed
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from backend.config import settings
from backend.core.logger import get_logger
from backend.db.connection import close_db, get_connection
from backend.tasks.ingestion_worker import IngestionStatus, ingest_document

logger = get_logger(__name__)

LEASE_SECONDS = settings.INGESTION_TASK_LEASE_SECONDS
MAX_ATTEMPTS = settings.INGESTION_MAX_ATTEMPTS
POLL_SECONDS = settings.INGESTION_QUEUE_POLL_SECONDS
# G10.8 M16：租约续期/周期回收间隔。续期必须明显小于租约，否则长任务
# 的 claimed_at 会在续期前过期，被周期回收误判为"worker 已死"重抢（双重摄取）。
RENEW_INTERVAL = max(1, LEASE_SECONDS // 3)


@dataclass
class ClaimedTask:
    """worker 抢占到的任务（attempts 已含本次）"""

    task_id: str
    document_id: str
    attempts: int


async def enqueue(document_id: str) -> bool:
    """确保文档有一个待处理任务；已存在非终态任务时不重复入队。

    终态（ready/failed）任务需重跑时（如失败重传），会新插入一条 pending。
    返回是否真正插入（True=入队，False=已存在非终态任务，幂等跳过）。

    G10.8 M15 原子化：此前是"SELECT 查重 → INSERT"两步，并发入队同一文档时
    两个调用都可能看到"无任务"然后各自插入 → 重复 pending 被两个 worker 抢走
    双重摄取。改为单条 `INSERT ... SELECT ... WHERE NOT EXISTS`，查重+插入
    在同一语句内完成（SQLite 单写者，隐式事务内原子）。
    """
    db = await get_connection()
    try:
        cur = await db.execute(
            "INSERT INTO ingestion_tasks (task_id, document_id, status) "
            "SELECT ?, ?, 'pending' "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM ingestion_tasks "
            "  WHERE document_id=? AND status NOT IN ('ready','failed')"
            ")",
            (str(uuid.uuid4()), document_id, document_id),
        )
        await db.commit()
        return cur.rowcount == 1
    finally:
        await close_db(db)


async def claim() -> ClaimedTask | None:
    """原子抢占最老的 pending 任务；无任务或已被抢走时返回 None。

    乐观锁：UPDATE 带 `AND status='pending'`，两个 worker 并发抢同一任务时
    只有一个 rowcount=1，另一个拿 0 → 重试下一轮。SQLite 单写者保证不重抢。
    """
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT task_id, document_id, attempts FROM ingestion_tasks "
            "WHERE status='pending' ORDER BY created_at ASC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        task_id, document_id, attempts = row
        cur = await db.execute(
            "UPDATE ingestion_tasks SET status='running', "
            "claimed_at=datetime('now'), attempts=attempts+1 "
            "WHERE task_id=? AND status='pending'",
            (task_id,),
        )
        if cur.rowcount == 0:
            return None  # 被其它 worker 抢走
        await db.commit()
        return ClaimedTask(task_id, document_id, attempts + 1)
    finally:
        await close_db(db)


async def recover_stale_tasks() -> int:
    """启动恢复：把上次崩溃/重启遗留的非终态任务统一处理。

    规则：
        - attempts 已达上限 → 终态 failed（对应文档同步标 failed，不再自动重试）
        - 其余（超租约的 running、旧版遗留的中间态 parsing/chunking/...）→ 回队 pending

    返回受影响任务数。须在 worker 启动前调用（单进程独占恢复窗口）。
    """
    db = await get_connection()
    try:
        # 1) 超次数：终态 failed + 文档同步 failed
        await db.execute(
            "UPDATE documents SET status='failed', "
            "error_msg='retry limit exceeded (recovered on startup)', "
            "updated_at=datetime('now') "
            "WHERE document_id IN (SELECT document_id FROM ingestion_tasks "
            "  WHERE status NOT IN ('ready','failed') AND attempts >= ?)",
            (MAX_ATTEMPTS,),
        )
        cur1 = await db.execute(
            "UPDATE ingestion_tasks SET status='failed', "
            "finished_at=datetime('now'), "
            "error_msg='retry limit exceeded (recovered on startup)' "
            "WHERE status NOT IN ('ready','failed') AND attempts >= ?",
            (MAX_ATTEMPTS,),
        )
        # 2) 未超次数：回队 pending（claimed_at 清空，可重新抢占）
        cur2 = await db.execute(
            "UPDATE ingestion_tasks SET status='pending', claimed_at=NULL "
            "WHERE status NOT IN ('ready','failed') "
            "AND (claimed_at IS NULL OR claimed_at < datetime('now', ?))",
            (f"-{LEASE_SECONDS} seconds",),
        )
        await db.commit()
        return int(cur1.rowcount) + int(cur2.rowcount)
    finally:
        await close_db(db)


# ---------------------------------------------------------------------------
# 租约续期 / 周期回收（G10.8 M16）
# ---------------------------------------------------------------------------


async def _renew_lease(task_id: str) -> None:
    """运行期租约续期（心跳）：每 RENEW_INTERVAL 前移 claimed_at。

    周期回收按 `claimed_at < now - LEASE` 判死——若长任务不续期，正常运行中的
    慢摄取也会被误判为 worker 崩溃而重抢（同一任务被两个 worker 同时摄取）。
    """
    try:
        while True:
            await asyncio.sleep(RENEW_INTERVAL)
            db = await get_connection()
            try:
                await db.execute(
                    "UPDATE ingestion_tasks SET claimed_at=datetime('now') "
                    "WHERE task_id=? AND status='running'",
                    (task_id,),
                )
                await db.commit()
            except Exception:  # noqa: BLE001 -- 续期失败下一轮再试，不阻断摄取
                logger.warning("lease renew failed for task %s", task_id)
            finally:
                await close_db(db)
    except asyncio.CancelledError:
        return


async def _reclaim_stale_leases() -> int:
    """运行期回收超租约的 running 任务回队 pending。

    G10.8 M16：`recover_stale_tasks` 只在启动时执行——worker 崩溃但进程未重启
    （如另一 worker 仍在运行）时，残留 running 任务会永久卡住，文档永不 ready。
    worker 循环内定期调用可自愈。超次数的终态判定仍由 claim 后的 `_run_task`
    负责（attempts >= MAX → failed），这里只负责回队。
    """
    db = await get_connection()
    try:
        cur = await db.execute(
            "UPDATE ingestion_tasks SET status='pending', claimed_at=NULL "
            "WHERE status='running' AND claimed_at < datetime('now', ?)",
            (f"-{LEASE_SECONDS} seconds",),
        )
        await db.commit()
        return cur.rowcount
    finally:
        await close_db(db)


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------


async def _document_exists(document_id: str) -> bool:
    db = await get_connection()
    try:
        async with db.execute("SELECT 1 FROM documents WHERE document_id=?", (document_id,)) as cur:
            return await cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False
    finally:
        await close_db(db)


async def _run_task(task: ClaimedTask) -> None:
    """执行一次摄取并落最终态。失败按 attempts 决定重试或终态。"""
    # 取文档元数据（归属用户 + 源文件路径）
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT stored_path, user_id FROM documents WHERE document_id=?",
            (task.document_id,),
        ) as cur:
            row = await cur.fetchone()
    finally:
        await close_db(db)
    if row is None:
        # 文档已被删除：终态 failed（重试无意义）
        await _finalize_failed(task, "document deleted before processing")
        return
    stored_path, user_id = row

    error_msg: str | None = None
    success = False
    # G10.8 M16：运行期租约续期（心跳）——慢任务不被周期回收误判重抢；
    # 任务结束（成功/失败）时取消续期，避免 dangling task
    renewal = asyncio.create_task(_renew_lease(task.task_id))
    try:
        status = await ingest_document(Path(stored_path), task.document_id, user_id=user_id)
        success = status == IngestionStatus.READY
        if not success:
            error_msg = "ingestion finished with non-ready status"
    except Exception as e:
        error_msg = str(e)[:500]
        logger.exception("task %s failed (attempt %d)", task.task_id, task.attempts)
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)

    if success:
        await _finalize_success(task)
        return
    if task.attempts >= MAX_ATTEMPTS or not await _document_exists(task.document_id):
        await _finalize_failed(task, error_msg or "ingestion failed")
    else:
        await _requeue(task.task_id)
        logger.info(
            "task %s requeued for retry (attempt %d/%d)",
            task.task_id,
            task.attempts,
            MAX_ATTEMPTS,
        )


async def _finalize_success(task: ClaimedTask) -> None:
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id=?", (task.document_id,)
        ) as cur:
            row = await cur.fetchone()
        chunk_count = int(row[0]) if row else 0
        await db.execute(
            "UPDATE documents SET status='ready', chunk_count=?, error_msg=NULL, "
            "updated_at=datetime('now') WHERE document_id=?",
            (chunk_count, task.document_id),
        )
        await db.execute(
            "UPDATE ingestion_tasks SET status='ready', finished_at=datetime('now'), "
            "error_msg=NULL WHERE task_id=?",
            (task.task_id,),
        )
        await db.commit()
        logger.info(
            "task %s done: document %s ready (%d chunks)",
            task.task_id,
            task.document_id[:12],
            chunk_count,
        )
    finally:
        await close_db(db)


async def _finalize_failed(task: ClaimedTask, error_msg: str) -> None:
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE documents SET status='failed', error_msg=?, "
            "updated_at=datetime('now') WHERE document_id=?",
            (error_msg, task.document_id),
        )
        await db.execute(
            "UPDATE ingestion_tasks SET status='failed', finished_at=datetime('now'), "
            "error_msg=? WHERE task_id=?",
            (error_msg, task.task_id),
        )
        await db.commit()
        logger.warning("task %s failed (final): %s", task.task_id, error_msg)
    finally:
        await close_db(db)


async def _requeue(task_id: str) -> None:
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE ingestion_tasks SET status='pending', claimed_at=NULL WHERE task_id=?",
            (task_id,),
        )
        await db.commit()
    finally:
        await close_db(db)


async def worker_loop() -> None:
    """worker 主循环：claim → 处理 → 睡眠。进程内 asyncio 任务或独立进程均可跑。"""
    logger.info(
        "ingestion worker started (poll=%ss lease=%ss max_attempts=%d)",
        POLL_SECONDS,
        LEASE_SECONDS,
        MAX_ATTEMPTS,
    )
    last_reclaim = 0.0
    while True:
        try:
            # G10.8 M16：周期性回收超租约 running 任务（worker 崩溃但进程未重启的自愈）
            if time.monotonic() - last_reclaim >= RENEW_INTERVAL:
                reclaimed = await _reclaim_stale_leases()
                if reclaimed:
                    logger.warning("reclaimed %d stale task(s)", reclaimed)
                last_reclaim = time.monotonic()
            task = await claim()
            if task is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            await _run_task(task)
        except asyncio.CancelledError:
            logger.info("ingestion worker stopped")
            raise
        except Exception:
            # 单次迭代失败不退出循环；记录后继续轮询
            logger.exception("worker loop iteration failed")
            await asyncio.sleep(POLL_SECONDS)
