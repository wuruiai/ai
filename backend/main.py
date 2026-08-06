"""应用入口

FastAPI 应用生命周期管理。

Reference: §8.1
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import perf_counter_ns

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.router import api_router
from backend.config import settings
from backend.core.logger import get_logger, set_request_id, setup_logging
from backend.core.metrics import instrument_request
from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from backend.tasks.queue import recover_stale_tasks, worker_loop

# 统一 JSON 结构化日志（幂等，首次导入即完成根 logger 配置）
setup_logging()
logger = get_logger(__name__)

# 前端构建产物目录（npm run build 输出）
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    # 生产环境密钥强校验：缺失关键 secret 直接拒绝启动（G1.3 fail-fast）
    settings.ensure_secrets()
    # 启动时：确保数据库迁移完成（幂等）、Chroma 集合可用
    logger.info("Starting Water RAG + Agent (app=%s)", app.title)
    try:
        db = await get_connection()
        try:
            await migrate(db)
            # G4.1：恢复上次崩溃遗留的摄取任务（超租约回队 / 超次数终态）
            recovered = await recover_stale_tasks()
            if recovered:
                logger.warning("Recovered %s stale ingestion task(s) on startup", recovered)
        finally:
            await close_db(db)
    except Exception:
        logger.exception("数据库初始化失败（继续启动，功能可能受限）")

    # G4.1：进程内 asyncio worker（本地默认）；生产可另起 scripts.worker 多进程
    _worker_task: asyncio.Task | None = None
    if settings.INGESTION_WORKER_IN_PROCESS:
        _worker_task = asyncio.create_task(worker_loop())

    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        from backend.rag.vector_store import silence_chroma_telemetry

        silence_chroma_telemetry()
        Path(settings.CHROMA_PATH).mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=settings.CHROMA_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        client.get_or_create_collection(settings.CHROMA_COLLECTION)
    except Exception:
        logger.exception("Chroma 初始化失败")

    yield
    logger.info("Shutting down...")
    # 停进程内 worker（先取消再等它退出，避免处理中途被掐）
    if _worker_task is not None:
        _worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await _worker_task
    # 关闭连接池（G4.2）：不关会导致 aiosqlite 后台线程拖住进程不退出（Docker stop 挂起）
    try:
        from backend.db.connection import _db_pool

        await _db_pool.close()
    except Exception:
        # 关闭兜底，不应阻止进程退出
        logger.exception("db pool close failed")


app = FastAPI(
    title="水利 RAG + Agent",
    description="水利行业知识问答系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置：统一从 settings.allowed_origins 取（含 localhost + 127.0.0.1 + 额外注入）
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """请求中间件：注入 X-Request-ID + 打点 Prometheus 指标。

    - 把 request_id 写入 contextvar，让本请求内所有结构化日志自动携带该字段（G2.1）。
    - 统计每个请求的耗时/状态码/路径到 http_requests_total / http_request_duration_seconds（G2.2）。
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    set_request_id(request_id)
    start_ns = perf_counter_ns()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        # 无论成功/异常都打点；异常时 call_next 抛错，状态记为 500 后继续上抛
        duration_s = (perf_counter_ns() - start_ns) / 1e9
        instrument_request(request.method, request.url.path, status, duration_s)
    response.headers["X-Request-ID"] = request_id
    return response


# 注册路由
app.include_router(api_router)


# ---------------------------------------------------------------------------
# 统一错误响应 envelope：{"error": {code, message, request_id}}
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"http_{exc.status_code}",
                "message": exc.detail,
                "request_id": request.headers.get("X-Request-ID"),
            }
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": f"{loc}: {first.get('msg', 'invalid')}",
                "request_id": request.headers.get("X-Request-ID"),
            }
        },
    )


# ---------------------------------------------------------------------------
# 前端静态资源伺服（生产形态：单端口访问 http://127.0.0.1:8001/）
# 仅当 frontend/dist 存在时挂载；否则跳过（开发用 vite dev server）。
# ---------------------------------------------------------------------------
if _FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """vue-router history 模式回退：非 API / health 路径返回 index.html。

        必须排除 /api/ 和 /health：这些路径应走真实路由或返回 404，
        不能被 SPA fallback 兜住（否则未知 API 路径会返回 200 index.html）。
        """
        if full_path.startswith(("api/", "health/")) or full_path == "health":
            # 交给上面的真实路由；若未匹配则 FastAPI 返回 404
            raise HTTPException(status_code=404, detail="Not Found")
        # 防路径穿越：拒绝含 .. 的路径（否则可读 frontend/dist/../../.env 等）
        if ".." in full_path:
            raise HTTPException(status_code=404, detail="Not Found")
        # 防 Windows 绝对路径绕过：Path(dist) / "C:/..." 会直接替换成盘符绝对路径，
        # 必须 resolve 后再校验是否仍落在 dist 目录内（否则可 GET /C:/.../.env 读任意文件）
        dist_root = _FRONTEND_DIST.resolve()
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and not candidate.is_relative_to(dist_root):
            raise HTTPException(status_code=404, detail="Not Found")
        # 优先返回真实存在的静态文件（vite.svg 等），否则回退 index.html
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist_root / "index.html")

else:
    logger.warning(
        "frontend/dist 不存在（%s），跳过静态伺服。开发模式请用: cd frontend && npm run dev",
        _FRONTEND_DIST,
    )
