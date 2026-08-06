"""管理看板：统计 + 审计查询（仅管理员）

面向运维/管理的只读聚合接口。
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.api.v1.auth import CurrentUser, require_admin
from backend.core.audit import write_audit
from backend.db.connection import close_db, get_connection

router = APIRouter()


class UserOut(BaseModel):
    user_id: str
    username: str
    display_name: str | None = None
    role: str
    is_active: int
    created_at: str | None = None


class UserUpdateRequest(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    is_active: int | None = Field(default=None, ge=0, le=1)


class UserListResponse(BaseModel):
    users: list[UserOut] = Field(default_factory=list)
    total: int = 0


class AdminStats(BaseModel):
    user_count: int = 0
    document_count: int = 0
    chunk_count: int = 0
    message_count: int = 0
    feedback_count: int = 0
    helpful_count: int = 0
    audit_count: int = 0


class AuditLogOut(BaseModel):
    log_id: str
    user_id: str | None = None
    username: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    detail: str | None = None
    created_at: str | None = None


class AuditListResponse(BaseModel):
    logs: list[AuditLogOut] = Field(default_factory=list)
    total: int = 0


@router.get("/stats", response_model=AdminStats)
async def admin_stats(_admin: CurrentUser = Depends(require_admin)) -> AdminStats:
    """系统统计：用户/文档/片段/消息/反馈/审计。"""
    db = await get_connection()
    try:

        async def _one(sql: str) -> int:
            async with db.execute(sql) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0

        user_count = await _one("SELECT COUNT(*) FROM users")
        document_count = await _one("SELECT COUNT(*) FROM documents")
        chunk_count = await _one("SELECT COUNT(*) FROM chunks")
        message_count = await _one("SELECT COUNT(*) FROM messages")
        feedback_count = await _one("SELECT COUNT(*) FROM feedback")
        helpful_count = await _one("SELECT COUNT(*) FROM feedback WHERE rating='helpful'")
        audit_count = await _one("SELECT COUNT(*) FROM audit_log")
        return AdminStats(
            user_count=user_count,
            document_count=document_count,
            chunk_count=chunk_count,
            message_count=message_count,
            feedback_count=feedback_count,
            helpful_count=helpful_count,
            audit_count=audit_count,
        )
    finally:
        await close_db(db)


@router.get("/users", response_model=UserListResponse)
async def admin_users(
    _admin: CurrentUser = Depends(require_admin),
) -> UserListResponse:
    """用户列表（管理用）。"""
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT id, username, display_name, role, is_active, created_at "
            "FROM users ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return UserListResponse(
            users=[
                UserOut(
                    user_id=r[0],
                    username=r[1],
                    display_name=r[2],
                    role=r[3],
                    is_active=r[4],
                    created_at=r[5],
                )
                for r in rows
            ],
            total=len(rows),
        )
    finally:
        await close_db(db)


@router.patch("/users/{user_id}", response_model=UserOut)
async def admin_update_user(
    user_id: str,
    body: UserUpdateRequest,
    admin: CurrentUser = Depends(require_admin),
) -> UserOut:
    """更新用户角色 / 启用状态。保护：不能禁用或降级自己（避免锁死最后一个管理员）。"""
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT id, username, display_name, role, is_active, created_at "
            "FROM users WHERE id=? LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="user not found")

        if user_id == admin.user_id:
            # 不允许禁用自己 / 把自己的角色降为非 admin
            if body.is_active is not None and body.is_active == 0:
                raise HTTPException(status_code=400, detail="不能禁用自己")
            if body.role is not None and body.role != "admin":
                raise HTTPException(status_code=400, detail="不能降级自己")

        sets: list[str] = []
        params: list = []
        if body.role is not None:
            sets.append("role=?")
            params.append(body.role)
        if body.is_active is not None:
            sets.append("is_active=?")
            params.append(body.is_active)
        if sets:
            params.append(user_id)
            # sets 全部来自固定模板字面量（role=?/is_active=?），值参数绑定 → 无注入面
            await db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", params)  # noqa: S608
            # 权限/状态变更后立即吊销该用户全部 token，让新角色/停用即刻生效（G1.4）
            await db.execute(
                "UPDATE users SET token_version = token_version + 1 WHERE id=?",
                (user_id,),
            )
            await db.execute(
                "UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (int(time.time()), user_id),
            )
            await db.commit()
            await write_audit(
                "admin.user_update",
                user_id=admin.user_id,
                username=admin.username,
                target_type="user",
                target_id=user_id,
                detail=f"role={body.role} is_active={body.is_active}",
            )
        return UserOut(
            user_id=row[0],
            username=row[1],
            display_name=row[2],
            role=body.role if body.role is not None else row[3],
            is_active=body.is_active if body.is_active is not None else row[4],
            created_at=row[5],
        )
    finally:
        await close_db(db)


@router.get("/audit", response_model=AuditListResponse)
async def admin_audit(
    _admin: CurrentUser = Depends(require_admin),
    limit: int = 50,
    offset: int = 0,
) -> AuditListResponse:
    """最近审计日志（分页）。"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT log_id, user_id, username, action, target_type, target_id, detail, created_at "
            "FROM audit_log ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        async with db.execute("SELECT COUNT(*) FROM audit_log") as cur:
            total = (await cur.fetchone())[0]
        return AuditListResponse(
            logs=[
                AuditLogOut(
                    log_id=r[0],
                    user_id=r[1],
                    username=r[2],
                    action=r[3],
                    target_type=r[4],
                    target_id=r[5],
                    detail=r[6],
                    created_at=r[7],
                )
                for r in rows
            ],
            total=total,
        )
    finally:
        await close_db(db)


# ---------------------------------------------------------------------------
# 数据导出（CSV）
# ---------------------------------------------------------------------------


def _csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/threads")
async def admin_export_threads(
    _admin: CurrentUser = Depends(require_admin),
) -> Response:
    """导出全部对话记录为 CSV。"""
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT m.created_at, u.username, m.thread_id, m.role, m.content "
            "FROM messages m LEFT JOIN users u ON u.id = m.user_id "
            "ORDER BY m.created_at, m.rowid"
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await close_db(db)
    return _csv_response(
        "threads_export.csv",
        ["created_at", "username", "thread_id", "role", "content"],
        [[str(r[0]), r[1] or "", r[2], r[3], r[4] or ""] for r in rows],
    )


@router.get("/export/feedback")
async def admin_export_feedback(
    _admin: CurrentUser = Depends(require_admin),
) -> Response:
    """导出反馈记录为 CSV。"""
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT f.created_at, u.username, f.rating, f.comment, m.thread_id "
            "FROM feedback f "
            "LEFT JOIN messages m ON m.message_id = f.message_id "
            "LEFT JOIN users u ON u.id = m.user_id "
            "ORDER BY f.created_at"
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await close_db(db)
    return _csv_response(
        "feedback_export.csv",
        ["created_at", "username", "rating", "comment", "thread_id"],
        [[str(r[0]), r[1] or "", r[2], r[3] or "", r[4] or ""] for r in rows],
    )


# ---------------------------------------------------------------------------
# 用量趋势（近 14 天）
# ---------------------------------------------------------------------------


class DailyStats(BaseModel):
    days: list[str] = Field(default_factory=list)
    messages: list[int] = Field(default_factory=list)
    uploads: list[int] = Field(default_factory=list)


@router.get("/stats/daily", response_model=DailyStats)
async def admin_stats_daily(
    _admin: CurrentUser = Depends(require_admin),
    days: int = 14,
) -> DailyStats:
    """近 N 天每日消息量与文档上传量趋势。"""
    days = max(7, min(days, 90))
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT date(created_at) d, COUNT(*) c FROM messages "
            "WHERE created_at >= date('now', ?) GROUP BY d",
            (f"-{days - 1} days",),
        ) as cur:
            msg_map = {r[0]: r[1] for r in await cur.fetchall()}
        async with db.execute(
            "SELECT date(created_at) d, COUNT(*) c FROM documents "
            "WHERE created_at >= date('now', ?) GROUP BY d",
            (f"-{days - 1} days",),
        ) as cur:
            doc_map = {r[0]: r[1] for r in await cur.fetchall()}
    finally:
        await close_db(db)

    today = date.today()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    return DailyStats(
        days=day_list,
        messages=[int(msg_map.get(d, 0)) for d in day_list],
        uploads=[int(doc_map.get(d, 0)) for d in day_list],
    )


# ---------------------------------------------------------------------------
# LLM 用量 / 成本（G3.1）
# ---------------------------------------------------------------------------


class UsageDay(BaseModel):
    day: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cny: float = 0.0


class UsageSummary(BaseModel):
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_cny: float = 0.0
    days: list[UsageDay] = Field(default_factory=list)


@router.get("/usage", response_model=UsageSummary)
async def admin_usage(
    _admin: CurrentUser = Depends(require_admin),
    days: int = 14,
) -> UsageSummary:
    """LLM token 用量与成本汇总（近 N 天趋势 + 累计）。"""
    days = max(1, min(days, 90))
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), "
            "COALESCE(SUM(output_tokens),0), COALESCE(SUM(cost_cny),0) "
            "FROM llm_usage"
        ) as cur:
            total = (await cur.fetchone()) or (0, 0, 0, 0)
        async with db.execute(
            "SELECT date(created_at) d, COUNT(*), SUM(input_tokens), "
            "SUM(output_tokens), SUM(cost_cny) FROM llm_usage "
            "WHERE created_at >= date('now', ?) GROUP BY d",
            (f"-{days - 1} days",),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await close_db(db)

    day_data = {
        r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0), float(r[4] or 0)) for r in rows
    }
    today = date.today()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    return UsageSummary(
        total_calls=int(total[0] or 0),
        total_input_tokens=int(total[1] or 0),
        total_output_tokens=int(total[2] or 0),
        total_cost_cny=round(float(total[3] or 0), 4),
        days=[
            UsageDay(
                day=d,
                calls=day_data.get(d, (0, 0, 0, 0.0))[0],
                input_tokens=day_data.get(d, (0, 0, 0, 0.0))[1],
                output_tokens=day_data.get(d, (0, 0, 0, 0.0))[2],
                cost_cny=round(day_data.get(d, (0, 0, 0, 0.0))[3], 4),
            )
            for d in day_list
        ],
    )
