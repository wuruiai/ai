"""摄取/删除 Chroma 写锁竞态测试（G9.4 幽灵向量防护）。

两个用例分别验证互斥锁的两个保证：
    1. 删除必须等摄取写完再清理（锁互斥，顺序严格 add → delete）；
    2. 摄取锁内重查文档仍存在，删除一旦提交则中止写入并回滚。
"""

import asyncio
from pathlib import Path

from backend.rag.vector_store import document_write_lock
from backend.tasks import ingestion_worker
from backend.tasks.ingestion_worker import IngestionStatus


async def _noop(*args, **kwargs):
    return None


async def _fake_parse(path):
    return [{"page": 1, "content": "水利工程防洪调度方案要点。" * 5}]


async def _fake_persist(document_id, chunks):
    return [f"{document_id}-{i}" for i in range(len(chunks))]


async def _locked_delete(doc_id, store) -> None:
    """复刻 delete_document 的锁内关键段（清向量；省略 DB 行删除，不影响互斥断言）。"""
    async with document_write_lock:
        await store.delete_by_document(doc_id)


def _isolate_worker(monkeypatch, exists, store) -> None:
    """把 worker 的 DB 触点全部换成内存替身，隔离真实数据库。"""
    monkeypatch.setattr(ingestion_worker, "_set_status", _noop)
    monkeypatch.setattr(ingestion_worker, "_parse", _fake_parse)
    monkeypatch.setattr(ingestion_worker, "_persist_chunks", _fake_persist)
    monkeypatch.setattr(ingestion_worker, "_rollback_chunks", _noop)
    monkeypatch.setattr(ingestion_worker, "_document_exists", exists)
    monkeypatch.setattr(ingestion_worker, "vector_store", store)


async def test_ingestion_and_delete_serialize_on_chroma_write(monkeypatch):
    """摄取 INDEXING 与删除共享互斥锁：删除必须等摄取写完，最后统一清理 → 无幽灵向量。"""
    add_started = asyncio.Event()
    release_add = asyncio.Event()
    order: list[str] = []

    class _FakeStore:
        async def add_documents(self, **kwargs):
            order.append("add:start")
            add_started.set()
            await release_add.wait()  # 持锁阻塞，模拟慢 Chroma 写
            order.append("add:done")

        async def delete_by_document(self, document_id):
            order.append("delete")
            return 1

    async def _exists(document_id):
        return True

    async def _embeddings(texts):
        return [[0.0] * 4] * len(texts)

    _isolate_worker(monkeypatch, _exists, _FakeStore())
    monkeypatch.setattr(ingestion_worker, "get_embeddings", _embeddings)

    worker = asyncio.create_task(ingestion_worker.ingest_document(Path("x.md"), "doc-race-1", "u1"))
    await asyncio.wait_for(add_started.wait(), timeout=2)  # 摄取已持锁进入 add_documents

    # 删除任务此刻必须等锁：不能插入到摄取写入中间
    deleter = asyncio.create_task(_locked_delete("doc-race-1", _FakeStore()))
    await asyncio.sleep(0.05)
    assert "delete" not in order, "删除不应在摄取持锁期间插入"

    release_add.set()
    await asyncio.wait_for(worker, timeout=2)
    await asyncio.wait_for(deleter, timeout=2)

    assert order == ["add:start", "add:done", "delete"], "严格先写后清 → 无幽灵向量"


async def test_ingest_aborts_if_deleted_before_locked_indexing(monkeypatch):
    """摄取锁内重查：文档在 embedding 之后、写向量之前被删 → 中止写入并回滚。"""
    add_called: list[str] = []
    exists = {"val": True}

    class _FakeStore:
        async def add_documents(self, **kwargs):
            add_called.append("add")

    async def _exists(document_id):
        return exists["val"]

    async def _embeddings(texts):
        return [[0.0] * 4] * len(texts)

    # 模拟删除恰在预检之后、锁内重查之前提交：_set_status(INDEXING) 时翻转
    async def _flip_on_indexing(document_id, status):
        if status == IngestionStatus.INDEXING:
            exists["val"] = False

    _isolate_worker(monkeypatch, _exists, _FakeStore())
    monkeypatch.setattr(ingestion_worker, "get_embeddings", _embeddings)
    monkeypatch.setattr(ingestion_worker, "_set_status", _flip_on_indexing)

    status = await ingestion_worker.ingest_document(Path("x.md"), "doc-race-2", "u1")

    assert status == IngestionStatus.FAILED
    assert add_called == [], "文档已删，不应写入任何向量（无幽灵向量）"
