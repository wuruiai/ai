"""管理看板 API 测试（统计/用户/导出/审计）。"""

from fastapi.testclient import TestClient

from backend.main import app


def _register(c: TestClient, name: str) -> dict:
    r = c.post("/api/v1/auth/register", json={"username": name, "password": "pass123456"})
    return r.json()


def test_admin_endpoints():
    with TestClient(app) as c:
        body = _register(c, "root_admin")
        assert body["user"]["role"] == "admin"  # 本测试内首个注册
        tok = body["token"]
        h = {"Authorization": f"Bearer {tok}"}

        assert c.get("/api/v1/admin/stats", headers=h).status_code == 200

        r = c.get("/api/v1/admin/users", headers=h)
        assert r.status_code == 200 and len(r.json()["users"]) >= 1

        r = c.get("/api/v1/admin/stats/daily", headers=h)
        assert r.status_code == 200 and len(r.json()["days"]) == 14

        r = c.get("/api/v1/admin/export/threads", headers=h)
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
        assert "created_at" in r.text

        r = c.get("/api/v1/admin/export/feedback", headers=h)
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"]

        r = c.get("/api/v1/admin/audit", headers=h)
        assert r.status_code == 200 and "logs" in r.json()


def test_admin_user_management():
    with TestClient(app) as c:
        admin = _register(c, "root_admin2")
        tok = admin["token"]
        h = {"Authorization": f"Bearer {tok}"}

        user = _register(c, "normal_user")
        assert user["user"]["role"] == "user"
        uid = user["user"]["user_id"]

        # 普通用户访问管理端点 → 403（角色鉴权在认证层之后生效）
        peon = _register(c, "peon")
        r = c.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {peon['token']}"})
        assert r.status_code == 403

        # 禁用用户 → 再登录 403（is_active=0 拒绝签发新 token）
        r = c.patch(f"/api/v1/admin/users/{uid}", json={"is_active": 0}, headers=h)
        assert r.status_code == 200
        r = c.post("/api/v1/auth/login", json={"username": "normal_user", "password": "pass123456"})
        assert r.status_code == 403

        # 禁用后 token_version 已 bump：禁用前签发的旧 token 立即失效 → 401（G1.4）
        r = c.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {user['token']}"})
        assert r.status_code == 401

        # 管理员不能禁用自己
        self_id = admin["user"]["user_id"]
        r = c.patch(f"/api/v1/admin/users/{self_id}", json={"is_active": 0}, headers=h)
        assert r.status_code == 400
