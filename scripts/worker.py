"""独立摄取 worker 进程（G4.1）

生产可多开（每进程一个 worker 循环），与 API 进程内的 worker 共用同一
SQLite 持久化队列；claim 原子抢占保证同一任务只被处理一次。

Usage:
    python -m scripts.worker

启动前先跑 migration + recover_stale_tasks（恢复上次崩溃遗留任务），
随后进入 worker_loop 循环。
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.logger import get_logger, setup_logging
from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from backend.tasks.queue import recover_stale_tasks, worker_loop

logger = get_logger(__name__)


async def _run() -> None:
    # 统一 JSON 结构化日志（与主进程一致）
    setup_logging()
    # 启动前确保 schema 就绪 + 恢复上次崩溃遗留任务（须在 worker 抢占前完成）
    db = await get_connection()
    try:
        await migrate(db)
    finally:
        await close_db(db)
    recovered = await recover_stale_tasks()
    if recovered:
        logger.warning("Recovered %s stale ingestion task(s)", recovered)
    await worker_loop()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("worker stopped by user")


if __name__ == "__main__":
    main()
