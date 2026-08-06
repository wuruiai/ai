"""文档 CRUD API 测试（mock 摄取，避免云端 embedding）。"""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.v1 import documents as doc_api
from backend.config import settings
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


def test_cross_user_duplicate_upload_409(monkeypatch):
    """S1：内容哈希幂等仅限同属主——他人已上传的同内容文件返回 409，不泄露元数据。"""
    monkeypatch.setattr(doc_api, "_spawn_ingestion", _noop_ingestion)
    with TestClient(app) as c:
        tokA, _ = _register(c, "dup_a")
        tokB, _ = _register(c, "dup_b")
        hA = {"Authorization": f"Bearer {tokA}"}
        hB = {"Authorization": f"Bearer {tokB}"}
        payload = b"same shared content bytes"

        rA = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", payload, "text/plain")},
            headers=hA,
        )
        assert rA.status_code == 201
        victim_id = rA.json()["document_id"]

        # 同用户重复上传 → 幂等返回原文档（非 409）
        r_same = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", payload, "text/plain")},
            headers=hA,
        )
        assert r_same.status_code == 201
        assert r_same.json()["document_id"] == victim_id

        # 跨用户上传同内容 → 409，且不泄露 victim 的 document_id
        rB = c.post(
            "/api/v1/documents/",
            files={"file": ("b.txt", payload, "text/plain")},
            headers=hB,
        )
        assert rB.status_code == 409
        assert victim_id not in rB.text


def _count_source_files(doc_id: str) -> list[str]:
    """统计 source/ 下该文档（内容哈希前缀）对应的落盘文件名。"""
    src = Path(settings.SOURCE_PATH)
    return [p.name for p in src.iterdir() if p.name.startswith(f"{doc_id}_")]


def test_duplicate_upload_leaves_no_orphan_file(monkeypatch):
    """G10.12：先查重后写盘——同用户以不同文件名重复上传同内容，不留下孤儿文件。"""
    monkeypatch.setattr(doc_api, "_spawn_ingestion", _noop_ingestion)
    with TestClient(app) as c:
        tok, _ = _register(c, "orphan_dup")
        h = {"Authorization": f"Bearer {tok}"}
        payload = b"orphan check content bytes"

        r1 = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", payload, "text/plain")},
            headers=h,
        )
        assert r1.status_code == 201
        doc_id = r1.json()["document_id"]

        # 同内容不同文件名 → 幂等返回原文档，且不应新增落盘副本
        r2 = c.post(
            "/api/v1/documents/",
            files={"file": ("b.txt", payload, "text/plain")},
            headers=h,
        )
        assert r2.status_code == 201
        assert r2.json()["document_id"] == doc_id

        files = _count_source_files(doc_id)
        # 只有首次上传的 a.txt 被引用；b.txt 不得残留（此前版本会留下孤儿文件）
        assert files == [f"{doc_id}_a.txt"]


def test_cross_user_duplicate_leaves_no_orphan_file(monkeypatch):
    """G10.12：跨用户 409 路径同样不写盘——他人同内容上传不得残留孤儿文件。"""
    monkeypatch.setattr(doc_api, "_spawn_ingestion", _noop_ingestion)
    with TestClient(app) as c:
        tokA, _ = _register(c, "orphan_a")
        tokB, _ = _register(c, "orphan_b")
        hA = {"Authorization": f"Bearer {tokA}"}
        hB = {"Authorization": f"Bearer {tokB}"}
        payload = b"cross orphan content bytes"

        rA = c.post(
            "/api/v1/documents/",
            files={"file": ("a.txt", payload, "text/plain")},
            headers=hA,
        )
        assert rA.status_code == 201
        doc_id = rA.json()["document_id"]

        # B 上传同内容不同文件名 → 409（S1 跨用户隔离）
        rB = c.post(
            "/api/v1/documents/",
            files={"file": ("zz.txt", payload, "text/plain")},
            headers=hB,
        )
        assert rB.status_code == 409

        # 只有 A 的 a.txt 被引用；B 的 zz.txt 不得落盘
        assert _count_source_files(doc_id) == [f"{doc_id}_a.txt"]
