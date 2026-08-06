"""健康检查 / 就绪检查 / Prometheus 指标（免令牌）

- `GET /health`        存活探针：进程在即 200（供 docker HEALTHCHECK / 负载均衡探活）
- `GET /health/ready`  就绪探针：SQLite + Chroma 依赖可达才 200，否则 503（供编排系统摘流）
- `GET /metrics`       Prometheus 指标文本（G2.2，供抓取）

Reference: §9.6 / §3.4
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.core.metrics import render_metrics
from backend.db.migrations import SCHEMA_VERSION

router = APIRouter()


async def _check_deps() -> dict:
    """轻量依赖检查：SQLite + Chroma 连通性。"""
    checks = {"database": False, "chroma": False}
    try:
        from backend.db.connection import close_db, get_connection

        db = await get_connection()
        try:
            async with db.execute("SELECT 1") as cur:
                await cur.fetchone()
            checks["database"] = True
        finally:
            await close_db(db)
    except Exception:  # noqa: BLE001
        checks["database"] = False
    try:
        from backend.rag.vector_store import vector_store

        await vector_store.count()
        checks["chroma"] = True
    except Exception:  # noqa: BLE001
        checks["chroma"] = False
    return checks


@router.get("/health")
async def health_check():
    """存活探针：进程在即 200（不含依赖检查，保持零成本、可高频探测）。"""
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


@router.get("/health/ready")
async def health_ready():
    """就绪探针：依赖（SQLite / Chroma）全部可达才 200，否则 503。"""
    checks = await _check_deps()
    ready = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if ready else "not_ready",
            "version": settings.APP_VERSION,
            "schema_version": SCHEMA_VERSION,
            "checks": checks,
        },
    )


@router.get("/metrics")
async def metrics():
    """Prometheus 指标（OpenMetrics 文本）。"""
    return Response(
        content=render_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
