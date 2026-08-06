"""会话（Thread）管理

对话历史管理：列出 / 新建 / 删除会话。

Reference: §9.2 会话管理

会话即 messages 表中的 thread_id 分组。标题取该会话第一条用户消息的前 20 字。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.core.security import validate_origin
from backend.db.connection import close_db, get_connection

router = APIRouter()


class ThreadInfo(BaseModel):
    thread_id: str
    title: str
    message_count: int
    created_at: str | None = None
    updated_at: str | None = None


class ThreadListResponse(BaseModel):
    threads: list[ThreadInfo] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50


@router.get("/", response_model=ThreadListResponse)
async def list_threads(
    user: CurrentUser = Depends(get_current_user),
    page: int = 1,
    page_size: int = 50,
) -> ThreadListResponse:
    """列出会话（普通用户仅自己的；管理员看全部）+ 分页。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    is_admin = user.role == "admin"
    db = await get_connection()
    try:
        # 按角色显式分支构造 SQL（值全部参数绑定；user_id 过滤即数据隔离）
        if is_admin:
            count_sql = "SELECT COUNT(*) FROM (SELECT thread_id FROM messages GROUP BY thread_id)"
            list_sql = (
                "SELECT t.thread_id, "
                "COALESCE((SELECT content FROM messages m2 "
                "WHERE m2.thread_id = t.thread_id AND m2.role = 'user' "
                "ORDER BY m2.created_at, m2.rowid LIMIT 1), '（空会话）') AS title, "
                "t.cnt, t.created_at, t.updated_at "
                "FROM (SELECT thread_id, COUNT(*) AS cnt, "
                "MIN(created_at) AS created_at, MAX(created_at) AS updated_at "
                "FROM messages GROUP BY thread_id) t "
                "ORDER BY t.updated_at DESC LIMIT ? OFFSET ?"
            )
            count_params: list = []
            list_params: list = [page_size, (page - 1) * page_size]
        else:
            count_sql = (
                "SELECT COUNT(*) FROM "
                "(SELECT thread_id FROM messages WHERE user_id = ? GROUP BY thread_id)"
            )
            list_sql = (
                "SELECT t.thread_id, "
                "COALESCE((SELECT content FROM messages m2 "
                "WHERE m2.thread_id = t.thread_id AND m2.role = 'user' "
                "AND m2.user_id = ? "
                "ORDER BY m2.created_at, m2.rowid LIMIT 1), '（空会话）') AS title, "
                "t.cnt, t.created_at, t.updated_at "
                "FROM (SELECT thread_id, COUNT(*) AS cnt, "
                "MIN(created_at) AS created_at, MAX(created_at) AS updated_at "
                "FROM messages WHERE user_id = ? GROUP BY thread_id) t "
                "ORDER BY t.updated_at DESC LIMIT ? OFFSET ?"
            )
            count_params = [user.user_id]
            list_params = [user.user_id, user.user_id, page_size, (page - 1) * page_size]

        async with db.execute(count_sql, count_params) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(list_sql, list_params) as cur:
            rows = await cur.fetchall()
    finally:
        await close_db(db)

    threads = []
    for r in rows:
        title = (r[1] or "").strip()
        if len(title) > 20:
            title = title[:20] + "…"
        threads.append(
            ThreadInfo(
                thread_id=r[0],
                title=title or "（新会话）",
                message_count=r[2],
                created_at=r[3],
                updated_at=r[4],
            )
        )
    # total 取全量计数（跨页），不是当前页条数 len(threads)
    return ThreadListResponse(threads=threads, total=total, page=page, page_size=page_size)


class MessageOut(BaseModel):
    message_id: str
    role: str
    content: str
    citations: list = Field(default_factory=list)
    created_at: str | None = None


class ThreadMessagesResponse(BaseModel):
    thread_id: str
    messages: list[MessageOut] = Field(default_factory=list)


@router.get("/{thread_id}/messages", response_model=ThreadMessagesResponse)
async def get_thread_messages(
    thread_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> ThreadMessagesResponse:
    """获取某会话的全部消息（时间正序）。

    返回 message_id + citations，供前端恢复引用面板和反馈按钮。
    数据隔离：仅本人（或管理员）可读。
    """
    db = await get_connection()
    try:
        if user.role == "admin":
            async with db.execute(
                "SELECT message_id, role, content, citations_json, created_at FROM messages "
                "WHERE thread_id=? ORDER BY created_at, rowid",
                (thread_id,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT message_id, role, content, citations_json, created_at FROM messages "
                "WHERE thread_id=? AND user_id=? ORDER BY created_at, rowid",
                (thread_id, user.user_id),
            ) as cur:
                rows = await cur.fetchall()
    finally:
        await close_db(db)

    messages: list[MessageOut] = []
    for r in rows:
        citations: list = []
        if r[3]:
            try:
                citations = json.loads(r[3])
            except (TypeError, ValueError):
                citations = []
        messages.append(
            MessageOut(
                message_id=r[0],
                role=r[1],
                content=r[2],
                citations=citations,
                created_at=r[4],
            )
        )
    return ThreadMessagesResponse(thread_id=thread_id, messages=messages)


@router.delete("/{thread_id}")
async def delete_thread(
    request: Request,
    thread_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """删除会话及其全部消息（仅本人或管理员）。"""
    validate_origin(request)
    db = await get_connection()
    try:
        if user.role == "admin":
            async with db.execute(
                "SELECT message_id FROM messages WHERE thread_id=? LIMIT 1", (thread_id,)
            ) as cur:
                exists = await cur.fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="thread not found")
            await db.execute(
                "DELETE FROM feedback WHERE message_id IN "
                "(SELECT message_id FROM messages WHERE thread_id=?)",
                (thread_id,),
            )
            await db.execute("DELETE FROM messages WHERE thread_id=?", (thread_id,))
        else:
            async with db.execute(
                "SELECT 1 FROM messages WHERE thread_id=? AND user_id=? LIMIT 1",
                (thread_id, user.user_id),
            ) as cur:
                exists = await cur.fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="thread not found")
            await db.execute(
                "DELETE FROM feedback WHERE message_id IN "
                "(SELECT message_id FROM messages WHERE thread_id=? AND user_id=?)",
                (thread_id, user.user_id),
            )
            await db.execute(
                "DELETE FROM messages WHERE thread_id=? AND user_id=?",
                (thread_id, user.user_id),
            )
        await db.commit()
    finally:
        await close_db(db)
    return {"status": "deleted", "thread_id": thread_id}
