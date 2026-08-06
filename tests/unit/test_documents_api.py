"""文档 CRUD API 测试（mock 摄取，避免云端 embedding）。"""

from fastapi.testclient import TestClient

from backend.api.v1 import documents as doc_api
from backend.main import app


async def _noop_ingestion(*a, **k):
    """摄取入队异步 no-op（测试不真正跑摄取/云端 embedding）。"""
    return None


def _register(c: TestClient, name: str) -> tuple[str, str]:
    r = c.post("/api/v1/auth/register", json={"username": name, "password": "pass123456"})
    body = r.json()
    return body["token"], body["user"]["user_id"]


def test_document_crud(monkeypatch):
    # 不真正跑摄取（会调云端 embedding）
    monkeypatch.setattr(doc_api, "_spawn_ingestion", _noop_ingestion)
    with TestClient(app) as c:
        tok, _ = _register(c, "doc_user")
        h = {"Authorization": f"Bearer {tok}"}

        # 空列表
        r = c.get("/api/v1/documents/", headers=h)
        assert r.status_code == 200 and r.json()["total"] == 0

        # 上传
        r = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", b"hello water content", "text/plain")},
            headers=h,
        )
        assert r.status_code == 201, r.text
        doc_id = r.json()["document_id"]

        # 查询
        r = c.get(f"/api/v1/documents/{doc_id}", headers=h)
        assert r.status_code == 200 and r.json()["file_name"] == "a.txt"

        # 非法扩展名 → 400
        r = c.post(
            "/api/v1/documents/",
            files={"file": ("a.exe", b"x", "application/octet-stream")},
            headers=h,
        )
        assert r.status_code == 400

        # PATCH 元数据（知识库结构化）
        r = c.patch(
            f"/api/v1/documents/{doc_id}", json={"category": "防洪", "is_enabled": 0}, headers=h
        )
        assert r.status_code == 200
        assert r.json()["category"] == "防洪"
        assert r.json()["is_enabled"] == 0

        # 删除
        r = c.delete(f"/api/v1/documents/{doc_id}", headers=h)
        assert r.status_code == 200
        r = c.get(f"/api/v1/documents/{doc_id}", headers=h)
        assert r.status_code == 404


def test_document_isolation(monkeypatch):
    """用户 A 上传的文档，用户 B 看不到也删不掉。"""
    monkeypatch.setattr(doc_api, "_spawn_ingestion", _noop_ingestion)
    with TestClient(app) as c:
        tokA, _ = _register(c, "iso_a")
        tokB, _ = _register(c, "iso_b")
        hA = {"Authorization": f"Bearer {tokA}"}
        hB = {"Authorization": f"Bearer {tokB}"}

        r = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", b"secret water doc", "text/plain")},
            headers=hA,
        )
        doc_id = r.json()["document_id"]

        # B 列表看不到
        r = c.get("/api/v1/documents/", headers=hB)
        assert r.json()["total"] == 0
        # B 查 A 的文档 → 404
        r = c.get(f"/api/v1/documents/{doc_id}", headers=hB)
        assert r.status_code == 404
        # B 删 A 的文档 → 404
        r = c.delete(f"/api/v1/documents/{doc_id}", headers=hB)
        assert r.status_code == 404


def test_upload_csrf_origin_blocked(monkeypatch):
    monkeypatch.setattr(doc_api, "_spawn_ingestion", _noop_ingestion)
    with TestClient(app) as c:
        tok, _ = _register(c, "csrf_user")
        r = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", b"x", "text/plain")},
            headers={"Authorization": f"Bearer {tok}", "Origin": "https://evil.example.com"},
        )
        assert r.status_code == 403  # 恶意 Origin 被拒
