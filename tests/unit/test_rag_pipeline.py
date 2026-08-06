"""摄取管线测试（mock embedding，走真实 SQLite + 临时 Chroma）。"""

from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from backend.tasks import ingestion_worker as iw


async def _fake_embeddings(texts):
    """mock embedding：返回与入参等长的固定向量（dim=4）。"""
    return [[0.1] * 4 for _ in texts]


async def _migrated_db():
    db = await get_connection()
    try:
        await migrate(db)
    finally:
        await close_db(db)


async def _insert_doc(doc_id: str, file_path) -> None:
    db = await get_connection()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO documents "
            "(document_id, file_name, stored_path, file_hash, file_size, mime_type, "
            " document_title, status, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                file_path.name,
                str(file_path),
                doc_id,
                10,
                "text/plain",
                file_path.stem,
                "pending",
                "u1",
            ),
        )
        await db.commit()
    finally:
        await close_db(db)


async def _count_chunks(doc_id: str) -> int:
    db = await get_connection()
    try:
        async with db.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)) as cur:
            return (await cur.fetchone())[0]
    finally:
        await close_db(db)


async def test_ingest_txt_ready(monkeypatch, tmp_path):
    await _migrated_db()
    monkeypatch.setattr(iw, "get_embeddings", _fake_embeddings)

    doc_id = "dtest123"
    fp = tmp_path / "a.txt"
    fp.write_text(
        "水利工程是治理水患的重要基础设施。水库调度需要统筹防洪与兴利。", encoding="utf-8"
    )
    await _insert_doc(doc_id, fp)

    status = await iw.ingest_document(fp, doc_id, user_id="u1")
    assert status == iw.IngestionStatus.READY
    assert await _count_chunks(doc_id) >= 1


async def test_ingest_docx_multi_paragraph_ready(monkeypatch, tmp_path):
    """回归：3 段 DOCX 之前因 chunk_id 碰撞必失败，现在应 ready。"""
    from docx import Document

    await _migrated_db()
    monkeypatch.setattr(iw, "get_embeddings", _fake_embeddings)

    d = Document()
    for t in ["第一段。", "第二段。", "第三段。"]:
        d.add_paragraph(t)
    fp = tmp_path / "b.docx"
    d.save(str(fp))

    doc_id = "dtestdocx"
    await _insert_doc(doc_id, fp)

    status = await iw.ingest_document(fp, doc_id, user_id="u1")
    assert status == iw.IngestionStatus.READY
    assert await _count_chunks(doc_id) >= 3  # 三段各一个 chunk
