"""认证：注册 / 登录 / 刷新 / 登出 + RBAC 依赖

企业级多用户（G1.1-G1.4）：
    - 密码 PBKDF2 哈希（带盐），不存明文
    - 角色 admin / user；首个注册用户自动成为管理员（bootstrap）
    - 标准 JWT（PyJWT HS256）：短时效 access token + 长时效 refresh token
    - refresh token 落库可吊销 → 支持登出与轮换（G1.2）
    - token_version：改密/权限变更后旧 token 立即失效（G1.4）
    - 登录防爆破：ip|username 双维度失败锁定（G1.1）
    - 生产环境启动强校验 TOKEN_SECRET / DASHSCOPE_API_KEY（G1.3，见 config.ensure_secrets）

Reference: §9.6 / §10.1
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.config import settings
from backend.core.audit import write_audit
from backend.core.logger import get_logger
from backend.core.security import validate_origin
from backend.db.connection import close_db, get_connection

logger = get_logger(__name__)
router = APIRouter()

# token 签名 secret：生产强制设置（config.ensure_secrets 启动时校验）；
# 开发留空时进程级随机（重启后旧 token 失效，仅限开发便利）
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
# JWT：access / refresh
# ---------------------------------------------------------------------------


def _create_token(payload: dict, expires_in_s: int) -> str:
    now = int(time.time())
    body = {
        **payload,
        "iat": now,
        "exp": now + expires_in_s,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(body, _process_secret, algorithm="HS256")


def create_access_token(user_id: str, username: str, role: str, token_version: int) -> str:
    """短时效 access token（默认 30 分钟，settings.ACCESS_TOKEN_TTL_S）。"""
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "ver": token_version,
        "type": "access",
    }
    return _create_token(payload, settings.ACCESS_TOKEN_TTL_S)


def create_refresh_token(user_id: str, token_version: int) -> str:
    """长时效 refresh token（默认 7 天，settings.REFRESH_TOKEN_TTL_S）。"""
    return _create_token(
        {"sub": user_id, "ver": token_version, "type": "refresh"},
        settings.REFRESH_TOKEN_TTL_S,
    )


def _decode_token(token: str, expected_type: str) -> dict:
    """校验签名/过期/类型；非法一律抛 401（不区分原因，避免 oracle）。"""
    try:
        payload = jwt.decode(token, _process_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type") from None
    return payload


# 兼容旧入口：verify_token 仅校验 access token 签名/过期（不做 ver 检查，调用方负责）
def verify_token(token: str) -> dict:
    """兼容旧接口：等价于 access token 的签名+过期校验。"""
    return _decode_token(token, "access")


# ---------------------------------------------------------------------------
# RBAC 依赖
# ---------------------------------------------------------------------------


class CurrentUser(BaseModel):
    user_id: str
    username: str
    role: str


async def _fetch_auth_row(user_id: str) -> tuple | None:
    """返回 (username, role, is_active, token_version)；不存在返回 None。"""
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT username, role, is_active, token_version FROM users WHERE id=? LIMIT 1",
            (user_id,),
        ) as cur:
            return await cur.fetchone()
    finally:
        await close_db(db)


async def get_current_user(authorization: str | None = Header(None)) -> CurrentUser:
    """强制鉴权：必须携带合法 access token，且用户未被停用/token 未被吊销。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    payload = _decode_token(token, "access")

    # 实时权威数据：角色/停用/版本均以 DB 为准，权限变更即时生效
    row = await _fetch_auth_row(payload.get("sub", ""))
    if row is None or not row[2]:
        raise HTTPException(status_code=401, detail="Invalid or disabled account")
    if payload.get("ver") != row[3]:
        raise HTTPException(status_code=401, detail="Token revoked, please login again")
    return CurrentUser(user_id=payload["sub"], username=row[0], role=row[1])


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """管理员权限依赖。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


# ---------------------------------------------------------------------------
# Token 生命周期辅助
# ---------------------------------------------------------------------------


async def _revoke_refresh_token(token_id: str) -> None:
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE refresh_tokens SET revoked_at=? WHERE token_id=?",
            (int(time.time()), token_id),
        )
        await db.commit()
    finally:
        await close_db(db)


async def _revoke_all_refresh_tokens(user_id: str) -> None:
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (int(time.time()), user_id),
        )
        await db.commit()
    finally:
        await close_db(db)


async def _bump_token_version(user_id: str) -> None:
    """bump 版本号：使该用户已签发的全部 access/refresh token 立即失效。"""
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE users SET token_version = token_version + 1 WHERE id=?",
            (user_id,),
        )
        await db.commit()
    finally:
        await close_db(db)


async def _issue_tokens(user_id: str, username: str, role: str) -> tuple[str, str]:
    """创建 access+refresh 对，并把 refresh 落库（可吊销）；返回 (access, refresh)。"""
    db = await get_connection()
    try:
        async with db.execute("SELECT token_version FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        ver = row[0] if row else 0
        access = create_access_token(user_id, username, role, ver)
        refresh = create_refresh_token(user_id, ver)
        refresh_payload = jwt.decode(refresh, _process_secret, algorithms=["HS256"])
        await db.execute(
            "INSERT INTO refresh_tokens (token_id, user_id, expires_at) VALUES (?, ?, ?)",
            (refresh_payload["jti"], user_id, refresh_payload["exp"]),
        )
        await db.commit()
        return access, refresh
    finally:
        await close_db(db)


def _token_response(
    user_id: str, username: str, role: str, display_name: str, access: str, refresh: str
) -> dict:
    return {
        # 兼容旧前端/旧测试的 `token` 字段（= access token）
        "token": access,
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_TTL_S,
        "user": _user_public(user_id, username, role, display_name),
    }


# ---------------------------------------------------------------------------
# 注册 / 登录 / 刷新 / 登出 / 当前用户
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=6, max_length=64)
    display_name: str = Field(default="", max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(default="", max_length=2048)
    all: bool = Field(default=False, description="true = 吊销该用户全部 refresh token")


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
    access, refresh = await _issue_tokens(user_id, req.username, role)
    await write_audit(
        "auth.register",
        user_id=user_id,
        username=req.username,
        target_type="user",
        target_id=user_id,
        detail=f"role={role}",
        ip=_client_ip(request),
    )
    return _token_response(
        user_id, req.username, role, req.display_name or req.username, access, refresh
    )


@router.post("/login")
async def login(req: LoginRequest, request: Request) -> dict:
    """登录：防爆破 → 校验密码 → access+refresh token。"""
    validate_origin(request)

    # 登录防爆破：ip|username 双维度（懒加载避免 auth↔rate_limit 循环导入）
    from backend.core.rate_limit import login_throttle

    ip_key = f"ip:{_client_ip(request) or 'unknown'}"
    user_key = f"user:{req.username}"
    login_throttle.check(ip_key)
    login_throttle.check(user_key)

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
        login_throttle.record_failure(ip_key)
        login_throttle.record_failure(user_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user_id, username, display_name, role, is_active, password_hash = row
    if not is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    if not _verify_password(req.password, password_hash):
        login_throttle.record_failure(ip_key)
        login_throttle.record_failure(user_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    login_throttle.record_success(ip_key)
    login_throttle.record_success(user_key)
    access, refresh = await _issue_tokens(user_id, username, role)
    await write_audit(
        "auth.login",
        user_id=user_id,
        username=username,
        target_type="user",
        target_id=user_id,
        ip=_client_ip(request),
    )
    return _token_response(user_id, username, role, display_name, access, refresh)


@router.post("/refresh")
async def refresh(req: RefreshRequest) -> dict:
    """用 refresh token 换取新的 access+refresh（轮换：旧 refresh 即吊销）。"""
    payload = _decode_token(req.refresh_token, "refresh")
    user_id = payload.get("sub", "")
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT username, role, token_version FROM users WHERE id=? LIMIT 1", (user_id,)
        ) as cur:
            user_row = await cur.fetchone()
        async with db.execute(
            "SELECT revoked_at FROM refresh_tokens WHERE token_id=? AND user_id=?",
            (payload["jti"], user_id),
        ) as cur:
            rt_row = await cur.fetchone()
    finally:
        await close_db(db)

    if user_row is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("ver") != user_row[2] or rt_row is None or rt_row[0] is not None:
        # 已吊销 / token_version 已变 → 整体拒绝（不回显具体原因）
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # 轮换：旧 refresh 一次性，避免重放
    await _revoke_refresh_token(payload["jti"])
    username, role = user_row[0], user_row[1]
    access, new_refresh = await _issue_tokens(user_id, username, role)
    await write_audit(
        "auth.refresh",
        user_id=user_id,
        username=username,
        target_type="user",
        target_id=user_id,
    )
    return _token_response(user_id, username, role, username, access, new_refresh)


@router.post("/logout")
async def logout(
    req: LogoutRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """登出：吊销 refresh token（可一次性吊销该用户全部）。"""
    if req.all:
        await _revoke_all_refresh_tokens(user.user_id)
    else:
        if not req.refresh_token:
            raise HTTPException(status_code=400, detail="refresh_token 必填（或 all=true）")
        payload = _decode_token(req.refresh_token, "refresh")
        if payload.get("sub") != user.user_id:
            raise HTTPException(status_code=400, detail="refresh token 不属于当前用户")
        await _revoke_refresh_token(payload["jti"])
    await write_audit(
        "auth.logout",
        user_id=user.user_id,
        username=user.username,
        target_type="user",
        target_id=user.user_id,
        detail="all" if req.all else "one",
    )
    return {"status": "ok"}


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
    """修改密码：改密后该用户全部 token 立即失效（含当前会话，需重新登录）。"""
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
    # 立即失效全部旧 token（含正在使用的 access token）
    await _bump_token_version(user.user_id)
    await _revoke_all_refresh_tokens(user.user_id)
    await write_audit(
        "auth.change_password",
        user_id=user.user_id,
        username=user.username,
        target_type="user",
        target_id=user.user_id,
    )
    return {"status": "ok"}
