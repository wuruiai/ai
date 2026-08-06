"""Chroma 向量存储测试（临时库；G4.3 后走 async 入口）。"""

from backend.rag.vector_store import VectorStore


def _fresh_store() -> VectorStore:
    # 用独立客户端指向 conftest 的临时 CHROMA_PATH
    return VectorStore()


async def test_vector_store_add_query_delete():
    vs = _fresh_store()
    # 清空集合，避免与其它测试共享同一 collection 造成计数干扰
    existing = vs._collection.get()["ids"]
    if existing:
        vs._collection.delete(ids=existing)
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
