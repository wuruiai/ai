"""文档摄取 worker（状态机驱动）

后台任务处理。


状态机：
    pending → parsing → chunking → embedding → indexing → ready
    任意阶段失败 → failed（documents.error_msg 记录原因）

设计原则：
    - 每个阶段更新一次 documents.status（便于 UI 实时展示）
    - chunks 由 FTS5 触发器自动同步（不允许应用层双写）
    - embeddings 走 Chroma；失败时回滚（删已写 chunks 防止孤儿数据）
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from backend.core.logger import get_logger
from backend.db.connection import close_db, get_connection
from backend.rag.chunker import chunk_pages
from backend.rag.embedding import get_embeddings
from backend.rag.ids import generate_chunk_id
from backend.rag.parser import parse_docx, parse_pdf
from backend.rag.vector_store import vector_store

logger = get_logger(__name__)


class IngestionStatus(StrEnum):
    """摄取状态"""

    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


async def _set_status(document_id: str, status: IngestionStatus) -> None:
    """更新 documents.status（短连接，频繁调用可控）。"""
    db = await get_connection()
    try:
        await db.execute(
            "UPDATE documents SET status=?, updated_at=datetime('now') WHERE document_id=?",
            (status.value, document_id),
        )
        await db.commit()
    finally:
        await close_db(db)


async def _document_exists(document_id: str) -> bool:
    """检查文档是否仍存在（防删除竞态：ingestion 进行中文档被删则中止）。"""
    db = await get_connection()
    try:
        async with db.execute("SELECT 1 FROM documents WHERE document_id=?", (document_id,)) as cur:
            return await cur.fetchone() is not None
    except Exception:  # noqa: BLE001 -- 查询失败按不存在处理，中止摄取（宁可失败不可重复写）
        return False
    finally:
        await close_db(db)


async def _persist_chunks(document_id: str, chunks: list[dict]) -> list[str]:
    """把 chunks 写入 SQLite；FTS 触发器自动同步。返回 chunk_id 列表。"""
    chunk_ids: list[str] = []
    db = await get_connection()
    try:
        for c in chunks:
            cid = generate_chunk_id(document_id, c.get("page", 1), c["chunk_index"])
            chunk_ids.append(cid)
            await db.execute(
                "INSERT OR REPLACE INTO chunks "
                "(chunk_id, document_id, content, page, chunk_index) VALUES (?, ?, ?, ?, ?)",
                (cid, document_id, c["content"], c.get("page"), c["chunk_index"]),
            )
        await db.commit()
    finally:
        await close_db(db)
    return chunk_ids


async def _rollback_chunks(document_id: str) -> None:
    """回滚：删除已写的 chunks（FTS 触发器同步）。"""
    db = await get_connection()
    try:
        await db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        await db.commit()
    finally:
        await close_db(db)


async def _parse(file_path: Path) -> list[dict]:
    """按扩展名分发到对应 parser。"""
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return await parse_pdf(file_path)
    if ext == ".docx":
        return await parse_docx(file_path)
    if ext == ".txt":
        # TXT：按行打页（每 50 行一页），简单但稳定
        text = file_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        pages: list[dict] = []
        page_size = 50
        for i in range(0, len(lines), page_size):
            chunk_text = "\n".join(lines[i : i + page_size])
            if chunk_text.strip():
                pages.append({"page": len(pages) + 1, "content": chunk_text})
        if not pages:
            pages.append({"page": 1, "content": text})
        return pages
    if ext == ".md":
        # MD：当作单页大文本（章节信息后续可加 heading 解析）
        text = file_path.read_text(encoding="utf-8", errors="replace")
        return [{"page": 1, "content": text}]
    raise ValueError(f"unsupported file type: {ext}")


async def ingest_document(
    file_path: Path, document_id: str, user_id: str = "local_user"
) -> IngestionStatus:
    """主入口：跑完整个状态机；返回最终状态。

    user_id：文档归属用户，写入 Chroma 元数据用于检索隔离。

    异常处理：任何阶段失败 → rollback + 返回 FAILED；
    顶层异常由 documents._start_ingestion 捕获并写 error_msg。
    """
    try:
        # 1. PARSING
        await _set_status(document_id, IngestionStatus.PARSING)
        pages = await _parse(file_path)
        if not pages:
            raise ValueError("parser returned no pages (file empty?)")
        # 解析（可能耗时）期间文档被删则中止
        if not await _document_exists(document_id):
            logger.info("document %s deleted during parse, abort ingestion", document_id[:12])
            return IngestionStatus.FAILED

        # 2. CHUNKING
        await _set_status(document_id, IngestionStatus.CHUNKING)
        chunks = chunk_pages(pages)
        # 过滤空白 chunk（空内容无法 embedding，且会导致 ids/embeddings 长度不一致）
        chunks = [c for c in chunks if c.get("content") and c["content"].strip()]
        if not chunks:
            raise ValueError("chunker returned no non-empty chunks")

        # 3. PERSIST chunks（让 FTS 触发器同步）
        chunk_ids = await _persist_chunks(document_id, chunks)
        contents = [c["content"] for c in chunks]

        # 4. EMBEDDING
        await _set_status(document_id, IngestionStatus.EMBEDDING)
        try:
            embeddings = await get_embeddings(contents)
        except Exception:
            await _rollback_chunks(document_id)
            raise
        # embedding（网络调用，可能耗时）期间文档被删则中止并回滚
        if not await _document_exists(document_id):
            logger.info("document %s deleted during embedding, rollback", document_id[:12])
            await _rollback_chunks(document_id)
            return IngestionStatus.FAILED

        # 5. INDEXING（chroma 写入；FTS 由 chunks 触发器自动同步，无需再调）
        await _set_status(document_id, IngestionStatus.INDEXING)
        try:
            await vector_store.add_documents(
                ids=chunk_ids,
                documents=contents,
                embeddings=embeddings,
                metadatas=[
                    {
                        "document_id": document_id,
                        "page": c.get("page", 1),
                        "user_id": user_id,
                    }
                    for c in chunks
                ],
            )
        except Exception:
            await _rollback_chunks(document_id)
            raise

        # 6. READY
        await _set_status(document_id, IngestionStatus.READY)
        logger.info("ingestion ready: %s, %d chunks", document_id, len(chunk_ids))
        return IngestionStatus.READY

    except Exception:
        logger.exception("ingestion failed for %s", document_id)
        # 不再 set_status：外层 _start_ingestion 会写 FAILED + error_msg
        raise
