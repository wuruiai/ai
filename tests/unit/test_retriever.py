"""检索器测试：归一化 + 用户隔离传参。"""

from typing import ClassVar

from backend.rag.retriever import _normalize, retrieve

# ---------- _normalize ----------


def test_normalize_minmax():
    assert _normalize([1.0, 3.0]) == [0.0, 1.0]


def test_normalize_single_is_half():
    assert _normalize([5.0]) == [0.5]


def test_normalize_flat_is_half():
    assert _normalize([2.0, 2.0, 2.0]) == [0.5, 0.5, 0.5]


# ---------- 用户隔离：where 必须带 user_id ----------


async def test_retrieve_user_scoping_dense(monkeypatch):
    from backend.rag import retriever as r

    async def fake_embedding(_q):
        return [0.1] * 8

    class FakeVS:
        seen: ClassVar[dict] = {}

        async def query(self, query_embedding, n_results, where):
            FakeVS.seen = where
            return {
                "ids": [["c1"]],
                "documents": [["内容"]],
                "metadatas": [[{"document_id": "d1"}]],
                "distances": [[0.1]],
            }

    class FakeBM25:
        seen = None

        async def search(self, query, top_k, document_id, user_id):
            FakeBM25.seen = user_id
            return []

    monkeypatch.setattr(r, "get_embedding", fake_embedding)
    monkeypatch.setattr(r, "vector_store", FakeVS())
    monkeypatch.setattr(r, "bm25_store", FakeBM25())

    res = await retrieve("q", top_k=5, user_id="u1")
    assert FakeVS.seen == {"user_id": "u1"}  # dense where 传了 user_id
    assert FakeBM25.seen == "u1"  # sparse 也传了 user_id
    assert len(res) == 1
    assert res[0].source == "dense"


async def test_retrieve_dense_failure_degrades_to_sparse(monkeypatch):
    """embedding 故障时应降级为仅稀疏检索，而非抛异常。"""
    from backend.rag import retriever as r

    async def bad_embedding(_q):
        raise RuntimeError("dashscope down")

    class FakeBM25:
        async def search(self, query, top_k, document_id, user_id):
            return [
                {
                    "chunk_id": "c2",
                    "document_id": "d2",
                    "page": 1,
                    "content": "内容",
                    "score": -1.0,
                }
            ]

    monkeypatch.setattr(r, "get_embedding", bad_embedding)
    monkeypatch.setattr(r, "bm25_store", FakeBM25())

    res = await retrieve("q", top_k=5, user_id="u1")
    assert len(res) == 1
    assert res[0].source == "sparse"


async def test_retrieve_excludes_disabled_documents(monkeypatch):
    """G10.7 M17：is_enabled=0 的文档不参与检索（禁用开关对检索真实生效）。"""
    from backend.rag import retriever as r

    async def fake_embedding(_q):
        return [0.1] * 8

    async def fake_disabled(_user_id):
        return {"d_disabled"}

    class FakeVS:
        async def query(self, query_embedding, n_results, where):
            return {
                "ids": [["c1", "c2"]],
                "documents": [["内容A", "内容B"]],
                "metadatas": [[{"document_id": "d_disabled"}, {"document_id": "d_ok"}]],
                "distances": [[0.1, 0.2]],
            }

    class FakeBM25:
        async def search(self, query, top_k, document_id, user_id):
            return [
                {
                    "chunk_id": "c3",
                    "document_id": "d_disabled",
                    "content": "稀疏禁用",
                    "score": 1.0,
                }
            ]

    monkeypatch.setattr(r, "get_embedding", fake_embedding)
    monkeypatch.setattr(r, "_disabled_document_ids", fake_disabled)
    monkeypatch.setattr(r, "vector_store", FakeVS())
    monkeypatch.setattr(r, "bm25_store", FakeBM25())

    res = await retrieve("q", top_k=5, user_id="u1")
    # 禁用文档（d_disabled）的 chunk 无论 dense/sparse 都被过滤
    assert [x.chunk_id for x in res] == ["c2"]


async def test_disabled_document_ids_sql():
    """G10.7 M17：禁用集合 SQL 与真实列名（user_id / is_enabled）匹配，支持全局/按用户。"""
    from backend.db.connection import close_db, get_connection
    from backend.rag.retriever import _disabled_document_ids

    db = await get_connection()
    try:
        await db.execute(
            "CREATE TABLE documents ("
            "document_id TEXT PRIMARY KEY, user_id TEXT, "
            "is_enabled INTEGER NOT NULL DEFAULT 1)"
        )
        await db.execute(
            "INSERT INTO documents (document_id, user_id, is_enabled) VALUES "
            "('d_off', 'u1', 0), ('d_on', 'u1', 1), ('d_off2', 'u2', 0)"
        )
        await db.commit()
    finally:
        await close_db(db)

    assert await _disabled_document_ids("u1") == {"d_off"}
    assert await _disabled_document_ids("u2") == {"d_off2"}
    # 全局（管理员检索）排除所有用户的禁用文档
    assert await _disabled_document_ids(None) == {"d_off", "d_off2"}
