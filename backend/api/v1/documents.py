"""文档 CRUD + 摄取任务

文档管理接口。

Reference: §9.6

P0 行为：
    POST   /api/v1/documents/        上传文件，异步启动 ingestion，立即返回 PENDING
    GET    /api/v1/documents/        列出所有文档
    GET    /api/v1/documents/{id}    查询单个文档（含状态、chunk_count、错误信息）
    DELETE /api/v1/documents/{id}    删除文档及其 chunks（FTS 触发器自动同步）
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.config import settings
from backend.core.audit import write_audit
from backend.core.logger import get_logger
from backend.core.rate_limit import check_rate_limit
from backend.core.security import validate_origin
from backend.db.connection import close_db, get_connection
from backend.rag.vector_store import vector_store
from backend.tasks.ingestion_worker import IngestionStatus
from backend.tasks.queue import enqueue

logger = get_logger(__name__)
router = APIRouter()

# 允许的扩展名（小写、含点）
_ALLOWED_EXTS: set[str] = {".pdf", ".docx", ".txt", ".md"}
_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB 单文件上限


async def _spawn_ingestion(document_id: str) -> None:
    """把文档入持久化摄取队列（G4.1）；worker 负责实际执行。

    入队幂等：已存在非终态任务时不重复插入。
    """
    await enqueue(document_id)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DocumentInfo(BaseModel):
    document_id: str
    file_name: str
    document_title: str
    file_size: int
    mime_type: str | None = None
    status: str
    error_msg: str | None = None
    chunk_count: int = 0
    created_at: str
    updated_at: str
    # 知识库结构化（v2）：分类 / 标签 / 启用开关
    category: str | None = None
    tags: str | None = None
    is_enabled: int = 1


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50


class DocumentUploadResponse(BaseModel):
    document_id: str
    file_name: str
    file_size: int
    file_hash: str
    status: str


class DocumentUpdateRequest(BaseModel):
    """文档元数据更新（知识库结构化）：分类 / 标签 / 启用开关。"""

    category: str | None = Field(default=None, max_length=64)
    tags: str | None = Field(default=None, max_length=200)
    is_enabled: int | None = Field(default=None, ge=0, le=1)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _calc_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _doc_row_to_info(row: tuple) -> DocumentInfo:
    """sqlite row -> DocumentInfo。列顺序匹配下面 SELECT。"""
    return DocumentInfo(
        document_id=row[0],
        file_name=row[1],
        document_title=row[2],
        file_size=row[3],
        mime_type=row[4],
        status=row[5],
        error_msg=row[6],
        chunk_count=row[7],
        created_at=row[8],
        updated_at=row[9],
        category=row[10],
        tags=row[11],
        is_enabled=row[12],
    )


_DOC_SELECT = (
    "document_id, file_name, document_title, file_size, mime_type, status, "
    "error_msg, chunk_count, created_at, updated_at, "
    "category, tags, is_enabled"
)


async def _count_chunks(db, document_id: str) -> int:
    async with db.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (document_id,)) as cur:
        row = await cur.fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    user: CurrentUser = Depends(get_current_user),
    status: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> DocumentListResponse:
    """列出文档；可选按状态/分类过滤 + 分页。普通用户仅看自己的；管理员看全部。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    db = await get_connection()
    try:
        conds: list[str] = []
        params: list = []
        if status:
            conds.append("status=?")
            params.append(status)
        if category:
            conds.append("category=?")
            params.append(category)
        if user.role != "admin":
            conds.append("user_id=?")
            params.append(user.user_id)
        # conds 全部来自固定模板字面量（status=?/category=?/user_id=?），值参数绑定 → 无注入面
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        async with db.execute(f"SELECT COUNT(*) FROM documents {where}", params) as cur:  # noqa: S608
            total = (await cur.fetchone())[0]
        async with db.execute(
            f"SELECT {_DOC_SELECT} FROM documents {where} "  # noqa: S608
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ) as cur:
            rows = await cur.fetchall()
        docs = [_doc_row_to_info(r) for r in rows]
        return DocumentListResponse(
            documents=docs,
            total=total,
            page=page,
            page_size=page_size,
        )
    finally:
        await close_db(db)


@router.post("/", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(check_rate_limit),
) -> DocumentUploadResponse:
    """上传文档，异步启动 ingestion。

    流程：
        1. 校验扩展名 / 大小
        2. 计算 sha256，按 hash 查重
        3. 写文件到 data/source/{document_id}_{filename}
        4. 插入 documents 记录（status=pending，归属当前用户）
        5. 入持久化摄取队列（_spawn_ingestion），由 worker 异步执行
    """
    # CSRF 防护：multipart/form-data 是 CORS "simple request"（不触发 preflight），
    # 恶意网页可跨站静默向本地注入文件；校验 Origin 必须在本项目白名单内。
    validate_origin(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type: {ext}; allowed={sorted(_ALLOWED_EXTS)}",
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {len(content)} > {_MAX_FILE_SIZE}",
        )

    file_hash = _calc_hash(content)
    document_id = file_hash  # 用 sha256 作主键，重复上传天然幂等

    # 用 INSERT OR IGNORE + rowcount 做原子查重，避免并发上传 TOCTOU 竞态
    # （sha256 主键天然幂等：并发同文件时第二个 insert 被忽略）
    db = await get_connection()
    source_dir = Path(settings.SOURCE_PATH)
    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 落盘：只用 filename 的 basename（剥离目录部分，防路径穿越），
        # document_id 前缀避免重名覆盖；原始 filename 仍用于展示
        safe_basename = Path(file.filename).name
        stored_path = source_dir / f"{document_id}_{safe_basename}"
        # 双重防护：确认落盘路径在 DATA_ROOT 内（security.validate_file_path）
        from backend.core.security import validate_file_path

        validate_file_path(str(stored_path))
        # 大文件写盘放线程池，避免阻塞事件循环（SSE 等并发请求被卡住）
        await asyncio.to_thread(stored_path.write_bytes, content)

        # 入库（幂等：document_id 即 file_hash，重复 upload 时 INSERT 被忽略）
        title = Path(file.filename).stem
        cur = await db.execute(
            "INSERT OR IGNORE INTO documents "
            "(document_id, file_name, stored_path, file_hash, file_size, mime_type, "
            " document_title, status, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                file.filename,
                str(stored_path),
                file_hash,
                len(content),
                file.content_type,
                title,
                IngestionStatus.PENDING.value,
                user.user_id,
            ),
        )
        await db.commit()

        if cur.rowcount == 0:
            # 已存在（并发上传或重复上传）
            async with db.execute(
                "SELECT document_id, file_name, file_size, status, stored_path, user_id "
                "FROM documents WHERE file_hash=? LIMIT 1",
                (file_hash,),
            ) as cur2:
                existing = await cur2.fetchone()
            if existing:
                doc_id, doc_name, doc_size, doc_status, stored, _ = existing
                # 失败/中断的文档允许"重传重试"：重新触发摄取（成功文档保持幂等返回）。
                # 此前失败/中间态的文档重复上传只会拿回旧状态，摄取永远不会重跑。
                if doc_status != IngestionStatus.READY.value and stored:
                    await _spawn_ingestion(doc_id)
                    return DocumentUploadResponse(
                        document_id=doc_id,
                        file_name=doc_name,
                        file_size=doc_size,
                        file_hash=file_hash,
                        status=IngestionStatus.PENDING.value,
                    )
                return DocumentUploadResponse(
                    document_id=doc_id,
                    file_name=doc_name,
                    file_size=doc_size,
                    file_hash=file_hash,
                    status=doc_status,
                )
    finally:
        await close_db(db)

    # 审计
    await write_audit(
        "document.upload",
        user_id=user.user_id,
        username=user.username,
        target_type="document",
        target_id=document_id,
        detail=file.filename,
    )

    # 入持久化摄取队列（worker 异步执行）
    await _spawn_ingestion(document_id)

    return DocumentUploadResponse(
        document_id=document_id,
        file_name=file.filename,
        file_size=len(content),
        file_hash=file_hash,
        status=IngestionStatus.PENDING.value,
    )


@router.patch("/{document_id}", response_model=DocumentInfo)
async def update_document(
    request: Request,
    document_id: str,
    body: DocumentUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
) -> DocumentInfo:
    """更新文档元数据（分类/标签/启用），仅本人或管理员。"""
    validate_origin(request)
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT user_id FROM documents WHERE document_id=?", (document_id,)
        ) as cur:
            owner = await cur.fetchone()
        if not owner:
            raise HTTPException(status_code=404, detail="document not found")
        if user.role != "admin" and owner[0] != user.user_id:
            raise HTTPException(status_code=404, detail="document not found")

        sets: list[str] = []
        params: list = []
        if body.category is not None:
            sets.append("category=?")
            params.append(body.category or None)
        if body.tags is not None:
            sets.append("tags=?")
            params.append(body.tags or None)
        if body.is_enabled is not None:
            sets.append("is_enabled=?")
            params.append(body.is_enabled)
        if sets:
            params.append(document_id)
            # sets 全部来自固定模板字面量（category=?/tags=?/is_enabled=?），值参数绑定 → 无注入面
            await db.execute(
                f"UPDATE documents SET {', '.join(sets)}, updated_at=datetime('now') "  # noqa: S608
                "WHERE document_id=?",
                params,
            )
            await db.commit()
            await write_audit(
                "document.update",
                user_id=user.user_id,
                username=user.username,
                target_type="document",
                target_id=document_id,
                detail=f"category={body.category} tags={body.tags} enabled={body.is_enabled}",
            )
    finally:
        await close_db(db)
    return await get_document(document_id, user)


@router.get("/{document_id}", response_model=DocumentInfo)
async def get_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> DocumentInfo:
    """查询单个文档（仅本人或管理员）。"""
    db = await get_connection()
    try:
        async with db.execute(
            # _DOC_SELECT 是模块常量列清单；document_id 走参数绑定 → 无注入面
            f"SELECT {_DOC_SELECT}, user_id FROM documents WHERE document_id=? LIMIT 1",  # noqa: S608
            (document_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="document not found")
        # 数据隔离：非管理员只能看自己的文档（_DOC_SELECT 现在 13 列，user_id 在 idx 13）
        if user.role != "admin" and row[13] != user.user_id:
            raise HTTPException(status_code=404, detail="document not found")
        return _doc_row_to_info(row)
    finally:
        await close_db(db)


@router.delete("/{document_id}")
async def delete_document(
    request: Request,
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """删除文档。

    清理三处：
        1. SQLite documents 行（chunks 由 FK CASCADE 删，FTS 触发器同步）
        2. Chroma 向量库（delete_by_document，防止幽灵结果）
        3. 源文件（data/source/）
    """
    validate_origin(request)
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT stored_path, user_id FROM documents WHERE document_id=?", (document_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="document not found")
        # 数据隔离：非管理员只能删自己的文档
        if user.role != "admin" and row[1] != user.user_id:
            raise HTTPException(status_code=404, detail="document not found")

        # 先清 Chroma 向量库：若失败则中止（不删 DB 行），避免"DB 已删、向量残留"的
        # 幽灵结果。此前顺序是"先删 DB 再清 Chroma，失败仅 warning 吞掉"，会留下
        # 永久幽灵向量且无法再删一次清理（DB 行已不存在）。
        try:
            removed = await vector_store.delete_by_document(document_id)
            if removed:
                logger.info("deleted %d vectors for document %s", removed, document_id[:12])
        except Exception as e:  # noqa: BLE001
            logger.error("failed to clean vector store for %s: %s", document_id, e)
            # 500 不向客户端泄露内部细节，from None 切断异常链
            raise HTTPException(status_code=500, detail="failed to clean vector store") from None

        await db.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
        await db.commit()
    finally:
        await close_db(db)

    # 尝试删除源文件（失败不影响 DB 一致性）
    try:
        Path(row[0]).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.warning("failed to remove stored file: %s", row[0])

    return {"status": "deleted", "document_id": document_id}
