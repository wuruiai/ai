"""POST /api/v1/feedback

反馈接口。


流程：
    1. 校验 message_id 在 messages 表真实存在（外键链）
    2. 校验 rating ∈ {helpful, not_helpful}
    3. 插入 feedback 表
"""

from __future__ import annotations

import uuid

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.core.security import validate_origin
from backend.db.connection import close_db, get_connection

router = APIRouter()


class FeedbackRequest(BaseModel):
    """反馈请求"""

    message_id: str = Field(..., min_length=1, description="被反馈消息的 ID")
    rating: str = Field(..., pattern="^(helpful|not_helpful)$", description="helpful | not_helpful")
    comment: str = Field(default="", max_length=2000)


async def _message_exists(db: aiosqlite.Connection, message_id: str) -> bool:
    """外键校验：message_id 必须在 messages 表里真实存在。"""
    try:
        async with db.execute(
            "SELECT 1 FROM messages WHERE message_id = ? LIMIT 1", (message_id,)
        ) as cur:
            row = await cur.fetchone()
            return row is not None
    except aiosqlite.OperationalError:
        # messages 表尚未迁移到含此字段的版本，视为外键链未就绪
        return False


@router.post("/")
async def submit_feedback(
    request: Request,
    feedback: FeedbackRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """提交反馈：外键校验 + 真插入。"""
    validate_origin(request)
    db = await get_connection()
    try:
        if not await _message_exists(db, feedback.message_id):
            # 拒绝任填 UUID，验证外键链（防伪造关联）
            raise HTTPException(status_code=404, detail="message_id not found")
        # 数据隔离：只能给属于自己的消息反馈（管理员除外），否则一律 404 不暴露存在性
        if user.role != "admin":
            async with db.execute(
                "SELECT user_id FROM messages WHERE message_id=? LIMIT 1",
                (feedback.message_id,),
            ) as cur:
                owner = await cur.fetchone()
            if not owner or owner[0] != user.user_id:
                raise HTTPException(status_code=404, detail="message_id not found")

        feedback_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO feedback (feedback_id, message_id, rating, comment) VALUES (?, ?, ?, ?)",
            (feedback_id, feedback.message_id, feedback.rating, feedback.comment),
        )
        await db.commit()
        return {
            "status": "ok",
            "feedback_id": feedback_id,
            "message_id": feedback.message_id,
            "rating": feedback.rating,
        }
    finally:
        await close_db(db)
