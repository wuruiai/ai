"""文档 CRUD API 测试（mock 摄取，避免云端 embedding）。"""

import asyncio
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

        # Content-Length 预检（G10.16）：超大文件在读盘前被 413 拒绝（不整读进内存）
        r = c.post(
            "/api/v1/documents/",
            files={"file": ("big.txt", b"tiny", "text/plain")},
            headers={**h, "Content-Length": str(101 * 1024 * 1024)},
        )
        assert r.status_code == 413

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


# ---------------------------------------------------------------------------
# G10.25：并发上传竞态（两个同内容上传同时通过查重，INSERT OR IGNORE 决出赢家）
# ---------------------------------------------------------------------------


class _PauseResult:
    """复刻 aiosqlite `execute` 的双协议（awaitable + async ctx mgr）。

    首个命中 `file_hash=?` 的 SELECT（查重）让出 50ms——修复/行为依据：
    upload_document 用 `INSERT OR IGNORE`（document_id 即 file_hash）保证并发同内容
    上传只有一个 INSERT 生效，第二个 rowcount=0 走竞态清理路径。让出窗口让两个
    请求都先通过查重、再先后 INSERT，确定性命中该竞态分支。
    """

    def __init__(self, inner, sql, args, kwargs, gate):
        self._inner = inner
        self._sql = sql
        self._args = args
        self._kwargs = kwargs
        self._gate = gate
        self._ctx = None

    async def _start(self):
        self._ctx = self._inner.execute(self._sql, *self._args, **self._kwargs)
        cur = await self._ctx.__aenter__()
        if not self._gate["paused"] and "file_hash=?" in self._sql:
            self._gate["paused"] = True
            await asyncio.sleep(0.05)
        return cur

    def __await__(self):
        return self._start().__await__()

    async def __aenter__(self):
        return await self._start()

    async def __aexit__(self, *exc):
        if self._ctx is not None:
            return await self._ctx.__aexit__(*exc)
        return False


class _ConnProxy:
    def __init__(self, inner, gate):
        self._inner = inner
        self._gate = gate

    def execute(self, sql, *args, **kwargs):
        return _PauseResult(self._inner, sql, args, kwargs, self._gate)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_concurrent_same_content_upload_creates_single_row(monkeypatch):
    """G10.25：两个同内容上传并发——仅一行文档、一个落盘文件、一次入队。

    竞态：两个请求都通过查重后，INSERT OR IGNORE 只有一个 rowcount=1 生效；
    另一个 rowcount=0 走竞态清理路径——清理自己写的孤儿文件、幂等返回权威记录、
    不再重复入队。此前该路径无测试，靠代码注释声明行为。
    """
    from io import BytesIO

    from starlette.datastructures import UploadFile
    from starlette.requests import Request

    from backend.api.v1.auth import CurrentUser
    from backend.db.connection import close_db
    from backend.db.connection import get_connection as raw_get_connection
    from backend.db.migrations import migrate

    # 直接调路由不经过 TestClient lifespan：先建表
    db = await raw_get_connection()
    try:
        await migrate(db)
    finally:
        await close_db(db)

    enqueued: list[str] = []

    async def _spy_spawn_ingestion(document_id: str):
        enqueued.append(document_id)

    monkeypatch.setattr(doc_api, "_spawn_ingestion", _spy_spawn_ingestion)

    # 竞态门：首个查重 SELECT 让出 50ms，制造"双双通过查重"窗口
    gate = {"paused": False}

    async def _proxied_get_connection():
        inner = await raw_get_connection()
        return _ConnProxy(inner, gate)

    monkeypatch.setattr(doc_api, "get_connection", _proxied_get_connection)

    content = b"concurrent upload race content"
    user = CurrentUser(user_id="u_race", username="race_user", role="user")

    def _scope() -> dict:
        return {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/api/v1/documents/",
            "raw_path": b"/api/v1/documents/",
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "headers": [],
        }

    req_a = Request(_scope())
    req_b = Request(_scope())
    file_a = UploadFile(file=BytesIO(content), filename="a.txt")
    file_b = UploadFile(file=BytesIO(content), filename="b.txt")

    r1, r2 = await asyncio.gather(
        doc_api.upload_document(request=req_a, file=file_a, user=user),
        doc_api.upload_document(request=req_b, file=file_b, user=user),
    )
    await file_a.close()
    await file_b.close()

    # 两个请求都幂等返回同一 document_id
    assert r1.document_id == r2.document_id
    doc_id = r1.document_id
    assert r1.status == "pending" and r2.status == "pending"

    # 仅一行文档记录
    db = await raw_get_connection()
    try:
        async with db.execute("SELECT COUNT(*) FROM documents WHERE file_hash=?", (doc_id,)) as cur:
            n = (await cur.fetchone())[0]
    finally:
        await close_db(db)
    assert n == 1

    # 仅一次入队：竞态失败方走 rowcount==0 清理路径，不重复入队
    assert enqueued == [doc_id]

    # 无孤儿文件：失败方写的 {hash}_b.txt 已被清理，只留权威记录引用的一个
    files = _count_source_files(doc_id)
    assert len(files) == 1
