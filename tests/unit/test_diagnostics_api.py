"""诊断接口权限测试。"""

from fastapi.testclient import TestClient

from backend.main import app


def test_diagnostics_requires_admin():
    with TestClient(app) as c:
        # 未认证 → 401
        assert c.get("/api/v1/diagnostics/").status_code == 401

        # 首个注册 → admin → 200 且不泄露完整 Key
        r = c.post(
            "/api/v1/auth/register", json={"username": "dia_admin", "password": "pass123456"}
        )
        assert r.json()["user"]["role"] == "admin"
        r = c.get("/api/v1/diagnostics/", headers={"Authorization": f"Bearer {r.json()['token']}"})
        assert r.status_code == 200
        body = r.json()
        assert "llm_model" in body and "api_key_configured" in body
        assert "sk-" not in r.text  # 不回显实际 Key

        # 普通用户 → 403
        r2 = c.post(
            "/api/v1/auth/register", json={"username": "dia_user", "password": "pass123456"}
        )
        r = c.get("/api/v1/diagnostics/", headers={"Authorization": f"Bearer {r2.json()['token']}"})
        assert r.status_code == 403
