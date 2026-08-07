"""Chroma 向量存储测试（临时库；G4.3 后走 async 入口）。"""

import pytest

from backend.rag.vector_store import VectorStore


def _fresh_store() -> VectorStore:
    # 用独立客户端指向 conftest 的临时 CHROMA_PATH
    return VectorStore()


def _clear_collection(vs: VectorStore) -> None:
    """清空集合，避免与其它测试共享同一 collection 造成计数干扰。"""
    existing = vs._collection.get()["ids"]
    if existing:
        vs._collection.delete(ids=existing)


async def test_vector_store_add_query_delete():
    vs = _fresh_store()
    _clear_collection(vs)
    await vs.add_documents(
        ids=["c1", "c2"],
        documents=["水利内容一", "水利内容二"],
        embeddings=[[0.1] * 4, [0.2] * 4],
        metadatas=[
            {"document_id": "d1", "user_id": "u1"},
            {"document_id": "d2", "user_id": "u1"},
        ],
    )
    assert await vs.count() == 2

    res = await vs.query([0.1] * 4, n_results=2)
    assert len(res["ids"][0]) == 2

    # 按用户过滤
    res = await vs.query([0.1] * 4, n_results=2, where={"user_id": "u1"})
    assert len(res["ids"][0]) == 2

    # 删除一个文档
    removed = await vs.delete_by_document("d1")
    assert removed == 1
    assert await vs.count() == 1


async def test_vector_store_upsert_idempotent():
    """G10.19：同 chunk_id 重摄取走 upsert 覆盖——不再抛 DuplicateIDError 全批失败。"""
    vs = _fresh_store()
    _clear_collection(vs)
    ids = ["i1", "i2"]
    # 正交 embedding：避免 [0.1]/[0.2] 同向（cosine 距离并列 0）导致查询排序不确定
    embs = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]

    await vs.add_documents(
        ids=ids,
        documents=["旧内容一", "旧内容二"],
        embeddings=embs,
        metadatas=[{"document_id": "d9"}, {"document_id": "d9"}],
    )
    assert await vs.count() == 2

    # 同 ids 重写（如失败重摄取）：upsert 覆盖，计数不变，不抛错
    await vs.add_documents(
        ids=ids,
        documents=["新内容一", "新内容二"],
        embeddings=embs,
        metadatas=[{"document_id": "d9"}, {"document_id": "d9"}],
    )
    assert await vs.count() == 2
    res = await vs.query([1.0, 0.0, 0.0, 0.0], n_results=2)
    assert res["documents"][0][0] == "新内容一"


async def test_vector_store_rollback_on_partial_write(monkeypatch):
    """G10.19：Chroma 批量写中途失败 → 回滚删除本次全部 ids，不残留孤儿向量。

    模拟：upsert 先真实写入 1 条再抛异常（部分写入）；期望 add_documents
    抛错后删除整个批次，集合回到 0——SQLite chunks 由摄取层回滚，两边都不留孤儿。
    """
    vs = _fresh_store()
    _clear_collection(vs)
    ids = ["r1", "r2"]
    real_add = vs._collection.add

    def flaky_upsert(**kwargs):
        real_add(
            ids=kwargs["ids"][:1],
            documents=kwargs["documents"][:1],
            embeddings=kwargs["embeddings"][:1],
            metadatas=kwargs["metadatas"][:1],
        )
        raise RuntimeError("mid-write failure")

    monkeypatch.setattr(vs._collection, "upsert", flaky_upsert)
    with pytest.raises(RuntimeError, match="mid-write failure"):
        await vs.add_documents(
            ids=ids,
            documents=["x", "y"],
            embeddings=[[0.3] * 4, [0.4] * 4],
            metadatas=[{"document_id": "d9"}, {"document_id": "d9"}],
        )
    # 批量回滚：本次 ids 全部清掉，无孤儿向量
    assert await vs.count() == 0
