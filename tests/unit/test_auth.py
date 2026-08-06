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
