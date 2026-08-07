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


def test_sanitize_csv_cell():
    """G10.22：CSV 公式注入前缀（= + - @ 等）被单引号转义，普通文本原样保留。"""
    from backend.api.v1 import admin as admin_api

    assert admin_api._sanitize_csv_cell("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert admin_api._sanitize_csv_cell("+123") == "'+123"
    assert admin_api._sanitize_csv_cell("-1") == "'-1"
    assert admin_api._sanitize_csv_cell("@cmd") == "'@cmd"
    assert admin_api._sanitize_csv_cell("\tTAB") == "'\tTAB"
    assert admin_api._sanitize_csv_cell("普通文本") == "普通文本"


def test_export_threads_sanitizes_formula_injection():
    """G10.22：导出对话 CSV 对公式注入内容加单引号前缀，Excel 不再当公式执行（DDE）。"""
    import sqlite3
    import uuid

    from backend.config import settings

    def _seed(content: str) -> None:
        conn = sqlite3.connect(settings.SQLITE_PATH)
        try:
            conn.execute(
                "INSERT INTO messages (message_id, thread_id, role, content, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "t_export", "user", content, "u_seed"),
            )
            conn.commit()
        finally:
            conn.close()

    with TestClient(app) as c:
        admin = _register(c, "csv_admin")
        h = {"Authorization": f"Bearer {admin['token']}"}
        _seed('=HYPERLINK("http://evil.example")')
        r = c.get("/api/v1/admin/export/threads", headers=h)
        assert r.status_code == 200
        # 转义前缀 `'` 已加（否则 Excel 打开会当外联公式执行）
        assert "'=HYPERLINK" in r.text
        # 负断言：不能出现「未被转义的 = 紧跟引号」形态（csv 引号包裹后的原始单元格）
        assert '"=HYPERLINK' not in r.text


def test_export_threads_row_cap(monkeypatch):
    """G10.22：导出行数上限——大库只导前 N 行（防整库读内存拼 CSV），N 可配。"""
    import sqlite3
    import uuid

    from backend.api.v1 import admin as admin_api
    from backend.config import settings

    monkeypatch.setattr(admin_api, "_EXPORT_MAX_ROWS", 2)

    def _seed() -> None:
        conn = sqlite3.connect(settings.SQLITE_PATH)
        try:
            for i in range(3):
                conn.execute(
                    "INSERT INTO messages (message_id, thread_id, role, content, user_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), "t_cap", "user", f"内容{i}", "u_seed"),
                )
            conn.commit()
        finally:
            conn.close()

    with TestClient(app) as c:
        admin = _register(c, "cap_admin")
        h = {"Authorization": f"Bearer {admin['token']}"}
        _seed()
        r = c.get("/api/v1/admin/export/threads", headers=h)
        assert r.status_code == 200
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        assert len(lines) == 1 + 2  # 表头 + 上限 2 行数据（共插 3 行）
