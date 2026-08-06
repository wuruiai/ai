"""GET /health（免令牌）

健康检查接口。

Reference: §9.6
"""

from fastapi import APIRouter

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

        vector_store.count()
        checks["chroma"] = True
    except Exception:  # noqa: BLE001
        checks["chroma"] = False
    return checks


@router.get("/health")
async def health_check():
    """健康检查（含 DB / Chroma 依赖状态）。"""
    checks = await _check_deps()
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "version": "1.0.0",
        "schema_version": SCHEMA_VERSION,
        "checks": checks,
    }
