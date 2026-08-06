"""GET /api/v1/diagnostics（脱敏）

诊断接口。

"""

from fastapi import APIRouter, Depends

from backend.api.v1.auth import require_admin
from backend.config import settings

router = APIRouter()


@router.get("/")
async def diagnostics(_admin: dict = Depends(require_admin)):
    """诊断信息（仅管理员）"""
    return {
        "app_env": settings.APP_ENV,
        "llm_model": settings.LLM_MODEL,
        "embedding_model": settings.EMBEDDING_MODEL,
        "rerank_model": settings.RERANK_MODEL,
        "api_key_configured": bool(settings.DASHSCOPE_API_KEY),
        # 不返回实际 Key
    }
