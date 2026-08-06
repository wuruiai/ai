"""审计日志写入

关键操作留痕（登录/注册/上传/删除/提问/设置等）。
写失败只告警不阻断业务。
"""

from __future__ import annotations

import uuid

from backend.core.logger import get_logger
from backend.db.connection import close_db, get_connection

logger = get_logger(__name__)


async def write_audit(
    action: str,
    *,
    user_id: str | None = None,
    username: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """写一条审计日志。异常只告警，不影响主流程。"""
    try:
        db = await get_connection()
        try:
            await db.execute(
                "INSERT INTO audit_log "
                "(log_id, user_id, username, action, target_type, target_id, detail, ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, username, action, target_type, target_id, detail, ip),
            )
            await db.commit()
        finally:
            await close_db(db)
    except Exception as e:  # noqa: BLE001
        logger.warning("audit write failed (action=%s): %s", action, e)
