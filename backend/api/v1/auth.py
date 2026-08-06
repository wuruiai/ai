"""认证：注册 / 登录 / 当前用户 + RBAC 依赖

企业级多用户：
    - 密码 PBKDF2 哈希（带盐），不存明文
    - 角色 admin / user；首个注册用户自动成为管理员（bootstrap）
    - 所有业务端点强制鉴权（get_current_user）；管理员接口 require_admin
    - token 为 HMAC 签名伪 JWT（进程级 secret，重启失效）

Reference: §9.6 / §10.1
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.config import settings
from backend.core.audit import write_audit
from backend.core.logger import get_logger
from backend.core.security import validate_origin
from backend.db.connection import close_db, get_connection

logger = get_logger(__name__)
router = APIRouter()

# token 签名 secret：配置了 TOKEN_SECRET 则跨重启稳定；否则进程级随机
_process_secret = settings.TOKEN_SECRET or uuid.uuid4().hex

_PBKDF2_ITERATIONS = 100_000


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def _verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iter_s, salt, expected = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iter_s))
        return hmac.compare_digest(dk.hex(), expected)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Token（HMAC 签名伪 JWT）
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign(header_b64: str, payload_b64: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(_process_secret.encode(), msg, hashlib.sha256).digest()
    return _b64url(sig)


def generate_token(user_id: str, username: str, role: str, expires_in_s: int = 86400) -> str:
    """生成伪 JWT（HMAC-SHA256），默认 24h 过期。"""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + expires_in_s,
        "jti": uuid.uuid4().hex,
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{header_b64}.{payload_b64}.{_sign(header_b64, payload_b64)}"


def verify_token(token: str) -> dict:
    """校验签名与过期时间；非法抛 401。"""
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid token")
    header_b64, payload_b64, sig_b64 = parts
    if not hmac.compare_digest(_sign(header_b64, payload_b64), sig_b64):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise HTTPException(status_code=401, detail="Token expired")
        return payload
    except (ValueError, json.JSONDecodeError):
        # 解析失败对客户端是同一个 401；from None 隐藏内部细节，避免异常链污染响应
        raise HTTPException(status_code=401, detail="Invalid token payload") from None


# ---------------------------------------------------------------------------
# RBAC 依赖
# ---------------------------------------------------------------------------


class CurrentUser(BaseModel):
    user_id: str
    username: str
    role: str


def get_current_user(authorization: str | None = Header(None)) -> CurrentUser:
    """强制鉴权：必须携带合法 Bearer token。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    payload = verify_token(token)
    return CurrentUser(
        user_id=payload.get("sub", ""),
        username=payload.get("username", ""),
        role=payload.get("role", "user"),
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """管理员权限依赖。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


# ---------------------------------------------------------------------------
# 注册 / 登录 / 当前用户
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=6, max_length=64)
    display_name: str = Field(default="", max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


def _user_public(user_id: str, username: str, role: str, display_name: str) -> dict:
    return {
        "user_id": user_id,
        "username": username,
        "role": role,
        "display_name": display_name,
    }


def _client_ip(request: Request) -> str | None:
    try:
        return request.client.host if request.client else None
    except Exception:  # noqa: BLE001
        return None


@router.post("/register")
async def register(req: RegisterRequest, request: Request) -> dict:
    """注册。首个注册用户自动成为管理员（bootstrap）。"""
    validate_origin(request)
    db = await get_connection()
    try:
        # 排除 v1 遗留的 local 种子用户：首个"注册"用户才成为管理员
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND username != 'local'"
        ) as cur:
            admin_count = (await cur.fetchone())[0]
        role = "admin" if admin_count == 0 else "user"
        user_id = str(uuid.uuid4())
        try:
            await db.execute(
                "INSERT INTO users (id, username, display_name, role, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    req.username,
                    req.display_name or req.username,
                    role,
                    _hash_password(req.password),
                ),
            )
            await db.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                # 用户名冲突对客户端是确定的 409；from None 不把 DB 异常链带给响应
                raise HTTPException(status_code=409, detail="username already exists") from None
            raise
    finally:
        await close_db(db)
    token = generate_token(user_id, req.username, role)
    await write_audit(
        "auth.register",
        user_id=user_id,
        username=req.username,
        target_type="user",
        target_id=user_id,
        detail=f"role={role}",
        ip=_client_ip(request),
    )
    return {
        "token": token,
        "user": _user_public(user_id, req.username, role, req.display_name or req.username),
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request) -> dict:
    """登录：校验密码 → token + 用户信息。"""
    validate_origin(request)
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT id, username, display_name, role, is_active, password_hash "
            "FROM users WHERE username=? LIMIT 1",
            (req.username,),
        ) as cur:
            row = await cur.fetchone()
    finally:
        await close_db(db)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user_id, username, display_name, role, is_active, password_hash = row
    if not is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    if not _verify_password(req.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = generate_token(user_id, username, role)
    await write_audit(
        "auth.login",
        user_id=user_id,
        username=username,
        target_type="user",
        target_id=user_id,
        ip=_client_ip(request),
    )
    return {"token": token, "user": _user_public(user_id, username, role, display_name)}


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """当前登录用户信息。"""
    return _user_public(user.user_id, user.username, user.role, user.username)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=64)


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """修改当前用户密码。"""
    db = await get_connection()
    try:
        async with db.execute("SELECT password_hash FROM users WHERE id=?", (user.user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="user not found")
        if not _verify_password(req.old_password, row[0]):
            raise HTTPException(status_code=400, detail="旧密码不正确")
        await db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (_hash_password(req.new_password), user.user_id),
        )
        await db.commit()
    finally:
        await close_db(db)
    await write_audit(
        "auth.change_password",
        user_id=user.user_id,
        username=user.username,
        target_type="user",
        target_id=user.user_id,
    )
    return {"status": "ok"}
