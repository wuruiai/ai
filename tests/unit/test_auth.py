"""认证/鉴权端到端（隔离临时 DB）。

覆盖 G1.1 登录防爆破 / G1.2 标准 JWT + refresh + logout / G1.4 token_version 立即失效。
"""

from fastapi.testclient import TestClient

from backend.main import app


def _register(c: TestClient, username: str, password: str = "pass123456") -> dict:
    r = c.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_register_login_change_password():
    with TestClient(app) as c:
        # 首个注册 → admin
        body = _register(c, "alice")
        assert body["user"]["role"] == "admin"
        # 新响应含 access_token / refresh_token 标准字段，且保留 token 兼容别名
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["token"] == body["access_token"]
        tok = body["access_token"]

        # 重复用户名 → 409
        r = c.post("/api/v1/auth/register", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 409

        # 登录
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 200
        assert r.json()["access_token"]

        # 错误密码 → 401（且不泄露用户是否存在）
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "wrongpass"})
        assert r.status_code == 401
        r = c.post("/api/v1/auth/login", json={"username": "no_such_user", "password": "wrongpass"})
        assert r.status_code == 401

        # 未认证访问受保护端点 → 401
        r = c.get("/api/v1/documents/")
        assert r.status_code == 401

        # 改密 → 成功后旧 token 立即失效（G1.4），需重新登录
        h = {"Authorization": f"Bearer {tok}"}
        r = c.post(
            "/api/v1/auth/change-password",
            json={"old_password": "pass123456", "new_password": "newpass123"},
            headers=h,
        )
        assert r.status_code == 200
        r = c.get("/api/v1/documents/", headers=h)
        assert r.status_code == 401, "改密后旧 access token 应立即失效"

        # 旧密码失效
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 401

        # 新密码可登录 → 取新 token
        body2 = c.post("/api/v1/auth/login", json={"username": "alice", "password": "newpass123"})
        assert body2.status_code == 200
        tok2 = body2.json()["access_token"]
        h2 = {"Authorization": f"Bearer {tok2}"}

        # 错误旧密码改密 → 400（用新 token）
        r = c.post(
            "/api/v1/auth/change-password",
            json={"old_password": "wrong", "new_password": "x123456"},
            headers=h2,
        )
        assert r.status_code == 400


def test_admin_requires_admin():
    with TestClient(app) as c:
        _register(c, "root")
        r = c.post("/api/v1/auth/register", json={"username": "bob", "password": "pass123456"})
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "user"
        tok = r.json()["access_token"]
        r = c.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403


def test_refresh_rotation_and_reuse_detection():
    with TestClient(app) as c:
        body = _register(c, "carol")
        refresh = body["refresh_token"]
        acc = body["access_token"]

        # refresh 换新 pair
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 200, r.text
        new = r.json()
        assert new["access_token"] != acc
        assert new["refresh_token"] != refresh

        # 旧 refresh 已轮换吊销 → 重放被拒
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 401

        # 用新 refresh 再换 → 成功
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]})
        assert r.status_code == 200


async def test_refresh_revoke_gate_is_atomic():
    """轮换吊销原子化：同一 refresh token 只允许一个吊销调用成功（并发双花防护）。

    模拟两个并发请求同时轮换同一 refresh token：第一个原子吊销成功（rowcount=1），
    第二个返回 0（已被用掉）；随后用该 token 调 /refresh 必然 401。
    """
    import jwt as pyjwt

    from backend.api.v1 import auth as auth_api

    with TestClient(app) as c:
        body = _register(c, "frank")
        refresh = body["refresh_token"]
        user_id = body["user"]["user_id"]
        payload = pyjwt.decode(refresh, auth_api._process_secret, algorithms=["HS256"])

        # 并发双请求：仅第一个能吊销成功
        assert await auth_api._revoke_refresh_token(payload["jti"], user_id) == 1
        assert await auth_api._revoke_refresh_token(payload["jti"], user_id) == 0

        # 已被吊销 → 重放 refresh 换新 401，绝不双花
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 401


def test_logout_revokes_refresh():
    with TestClient(app) as c:
        body = _register(c, "dave")
        acc, refresh = body["access_token"], body["refresh_token"]
        h = {"Authorization": f"Bearer {acc}"}

        # logout（带 refresh_token）→ 该 refresh 被吊销
        r = c.post("/api/v1/auth/logout", json={"refresh_token": refresh}, headers=h)
        assert r.status_code == 200
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 401

        # access token 本身仍有效（OAuth 语义：access 短时效自过期）
        r = c.get("/api/v1/auth/me", headers=h)
        assert r.status_code == 200

        # logout all → 全部 refresh 吊销（模拟“登出所有设备”）
        body2 = _register(c, "eve")
        acc2, refresh2 = body2["access_token"], body2["refresh_token"]
        h2 = {"Authorization": f"Bearer {acc2}"}
        r = c.post("/api/v1/auth/logout", json={"all": True}, headers=h2)
        assert r.status_code == 200
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": refresh2})
        assert r.status_code == 401


def test_admin_role_change_revokes_tokens():
    """管理员降级普通用户 → 其 token 立即失效（G1.4 权限即时生效）。"""
    with TestClient(app) as c:
        admin = _register(c, "root")
        user = _register(c, "sam")
        uh = {"Authorization": f"Bearer {user['access_token']}"}

        # 变更前：普通用户能访问自己的资源
        r = c.get("/api/v1/auth/me", headers=uh)
        assert r.status_code == 200

        # 管理员把 sam 降为 user（本来就是 user，改成显式设置 + 停用一次更直观）
        # 先确认 admin 有效
        ah = {"Authorization": f"Bearer {admin['access_token']}"}
        url = f"/api/v1/admin/users/{user['user']['user_id']}"
        r = c.patch(url, json={"role": "user"}, headers=ah)
        assert r.status_code == 200

        # 目标用户旧 token 失效（token_version 已 bump）
        r = c.get("/api/v1/auth/me", headers=uh)
        assert r.status_code == 401

        # 重新登录后恢复
        body = c.post("/api/v1/auth/login", json={"username": "sam", "password": "pass123456"})
        assert body.status_code == 200
        me_h = {"Authorization": f"Bearer {body.json()['access_token']}"}
        r = c.get("/api/v1/auth/me", headers=me_h)
        assert r.status_code == 200


def test_login_brute_force_lockout():
    """同一 username 连续失败超限后锁定 → 429 + Retry-After（防爆破）。"""
    with TestClient(app) as c:
        _register(c, "mallory")
        for _ in range(5):
            r = c.post("/api/v1/auth/login", json={"username": "mallory", "password": "wrong"})
            assert r.status_code == 401

        # 第 6 次即使密码正确也被锁定（username 维度已锁定）
        r = c.post("/api/v1/auth/login", json={"username": "mallory", "password": "pass123456"})
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1

        # 同一来源 IP 也触发 IP 维度锁定（防 IP 洪水）——username 维度隔离见 test_rate_limit.py
        _register(c, "nina")
        r = c.post("/api/v1/auth/login", json={"username": "nina", "password": "pass123456"})
        assert r.status_code == 429


def test_login_lockout_scoped_to_source_ip():
    """S1：用户名锁定按来源 IP 隔离——IP A 的 5 次失败只锁 (A, 账户) 组合，
    不再全局锁死账户：IP B 用正确密码仍可登录（修复前 user:{username} 无 IP 前缀，
    任意来源 IP 凑满 5 次即可远程锁死该账户并循环续期）。"""
    with TestClient(app) as c:
        _register(c, "scope_victim")
        attacker = {"X-Forwarded-For": "198.51.100.10"}

        for _ in range(5):
            r = c.post(
                "/api/v1/auth/login",
                json={"username": "scope_victim", "password": "wrong"},
                headers=attacker,
            )
            assert r.status_code == 401

        # 攻击者来源 IP 对该账户已锁定（user:{ip}:scope_victim + ip:{ip} 双维度）
        r = c.post(
            "/api/v1/auth/login",
            json={"username": "scope_victim", "password": "pass123456"},
            headers=attacker,
        )
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1

        # 不同来源 IP 不受该锁定影响：正确密码仍可登录（S1 回归点）
        r = c.post(
            "/api/v1/auth/login",
            json={"username": "scope_victim", "password": "pass123456"},
            headers={"X-Forwarded-For": "198.51.100.11"},
        )
        assert r.status_code == 200
        assert r.json()["access_token"]


def test_register_rate_limited_by_ip():
    """G10.5 M3：注册按 IP 滑动窗口限流——窗口内第 N+1 次注册返回 429。

    用 X-Forwarded-For 模拟独立来源 IP，与其余测试（testclient 默认 IP）隔离计数。
    """
    from backend.config import settings

    ip = "203.0.113.77"
    headers = {"X-Forwarded-For": ip}
    with TestClient(app) as c:
        for i in range(settings.REGISTER_MAX_PER_WINDOW):
            r = c.post(
                "/api/v1/auth/register",
                json={"username": f"spam_{i}", "password": "pass123456"},
                headers=headers,
            )
            assert r.status_code == 200, r.text

        # 超限：第 N+1 次注册被拒（同一 IP），且带 Retry-After
        r = c.post(
            "/api/v1/auth/register",
            json={"username": "spam_overflow", "password": "pass123456"},
            headers=headers,
        )
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1

        # 不同 IP 不受影响（限流按 IP 隔离）
        r = c.post(
            "/api/v1/auth/register",
            json={"username": "other_ip_ok", "password": "pass123456"},
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        assert r.status_code == 200


def test_register_disabled_when_allowed_flag_off(monkeypatch):
    """G10.5 M4：ALLOW_REGISTRATION=false 时注册返回 403（生产可关开放注册）。"""
    from backend.config import settings

    monkeypatch.setattr(settings, "ALLOW_REGISTRATION", False)
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/register", json={"username": "nobody", "password": "pass123456"})
        assert r.status_code == 403


def test_admin_bootstrap_username_explicit(monkeypatch):
    """G10.5 M4：配置 ADMIN_BOOTSTRAP_USERNAME 后，仅该用户名的首个注册者获得 admin。"""
    from backend.config import settings

    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_USERNAME", "root")
    with TestClient(app) as c:
        # 攻击者先注册别的名字 → 拿不到 admin
        r = c.post("/api/v1/auth/register", json={"username": "attacker", "password": "pass123456"})
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "user"

        # 配置名注册 → admin
        r = c.post("/api/v1/auth/register", json={"username": "root", "password": "pass123456"})
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "admin"


def test_client_ip_prefers_x_forwarded_for(monkeypatch):
    """G10.5 M6 + G10.17：仅当直连 peer 是可信代理时才采信 X-Forwarded-For。

    可信反代场景取 XFF 最左侧（真实客户端）；直连（无该头）回退 socket 地址；
    peer 不在 TRUSTED_PROXIES 内时伪造的 XFF 一律忽略（防 IP 旋转绕过限流）。
    """
    from starlette.requests import Request

    from backend.api.v1 import auth as auth_api
    from backend.config import settings

    def _make(xff: str | None) -> Request:
        headers = []
        if xff is not None:
            headers.append((b"x-forwarded-for", xff.encode()))
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/auth/register",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("192.168.1.99", 54321),
            "server": ("127.0.0.1", 8001),
        }
        return Request(scope)

    # 直连 peer 192.168.1.99 是可信代理 → 采信 XFF 最左侧（真实客户端）
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "192.168.1.99")
    assert auth_api._client_ip(_make("203.0.113.50")) == "203.0.113.50"
    # 多级反代：取最左侧（真实客户端），右侧为各级代理
    assert auth_api._client_ip(_make("203.0.113.50, 10.0.0.2, 10.0.0.1")) == "203.0.113.50"
    # 直连（无反代头）→ 回退 socket 地址
    assert auth_api._client_ip(_make(None)) == "192.168.1.99"

    # G10.17：peer 不在可信列表 → 伪造的 XFF 被忽略，回退直连地址
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    assert auth_api._client_ip(_make("6.6.6.6")) == "192.168.1.99"
