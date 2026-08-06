"""认证/鉴权端到端（隔离临时 DB）。"""

from fastapi.testclient import TestClient

from backend.main import app


def test_auth_register_login_change_password():
    with TestClient(app) as c:
        # 首个注册 → admin
        r = c.post("/api/v1/auth/register", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["role"] == "admin"
        tok = body["token"]

        # 重复用户名 → 409
        r = c.post("/api/v1/auth/register", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 409

        # 登录
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 200

        # 错误密码
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "wrongpass"})
        assert r.status_code == 401

        # 未认证访问受保护端点 → 401
        r = c.get("/api/v1/documents/")
        assert r.status_code == 401

        # 改密
        h = {"Authorization": f"Bearer {tok}"}
        r = c.post(
            "/api/v1/auth/change-password",
            json={"old_password": "pass123456", "new_password": "newpass123"},
            headers=h,
        )
        assert r.status_code == 200

        # 旧密码失效
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pass123456"})
        assert r.status_code == 401

        # 新密码可登录
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "newpass123"})
        assert r.status_code == 200

        # 错误旧密码改密 → 400
        r = c.post(
            "/api/v1/auth/change-password",
            json={"old_password": "wrong", "new_password": "x123456"},
            headers=h,
        )
        assert r.status_code == 400


def test_admin_requires_admin():
    with TestClient(app) as c:
        # 先注册 admin（本测试内首个），再注册普通用户
        c.post("/api/v1/auth/register", json={"username": "root", "password": "pass123456"})
        r = c.post("/api/v1/auth/register", json={"username": "bob", "password": "pass123456"})
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "user"
        tok = r.json()["token"]
        # 普通用户访问管理端点 → 403
        r = c.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403
