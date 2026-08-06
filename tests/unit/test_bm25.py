"""BM25 FTS5 检索测试（临时库 + 触发器同步）。"""

from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from backend.rag.bm25_store import BM25Store


async def _seed():
    db = await get_connection()
    try:
        await migrate(db)
        await db.execute(
            "INSERT INTO documents "
            "(document_id, file_name, stored_path, file_hash, file_size, mime_type, "
            " document_title, status, user_id) "
            "VALUES ('d1', 'a.txt', '/tmp/a.txt', 'h1', 10, 'text/plain', 'a', 'ready', 'u1')"
        )
        await db.execute(
            "INSERT INTO chunks (chunk_id, document_id, content, page, chunk_index) "
            "VALUES ('c1', 'd1', '水利工程是治理水患的重要基础设施。', 1, 0)"
        )
        await db.execute(
            "INSERT INTO chunks (chunk_id, document_id, content, page, chunk_index) "
            "VALUES ('c2', 'd1', '水库调度需要统筹防洪与兴利。', 1, 1)"
        )
        await db.commit()
    finally:
        await close_db(db)


async def test_bm25_search_returns_hits():
    await _seed()
    results = await BM25Store().search("水利工程", top_k=5, user_id="u1")
    assert any("水利工程" in r["content"] for r in results)


async def test_bm25_user_scoping():
    await _seed()
    # 其他用户看不到 d1 的内容（JOIN documents.user_id 过滤）
    results = await BM25Store().search("水利工程", top_k=5, user_id="u_other")
    assert results == []
