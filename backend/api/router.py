"""路由汇总

API 路由注册。

"""

from fastapi import APIRouter

from backend.api.v1 import (
    admin,
    auth,
    chat,
    diagnostics,
    documents,
    feedback,
    health,
    threads,
    unified_chat,
)

api_router = APIRouter()

# 注册 v1 路由
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
api_router.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
api_router.include_router(unified_chat.router, prefix="/api/v1/unified-chat", tags=["unified-chat"])
api_router.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
api_router.include_router(feedback.router, prefix="/api/v1/feedback", tags=["feedback"])
api_router.include_router(diagnostics.router, prefix="/api/v1/diagnostics", tags=["diagnostics"])
api_router.include_router(threads.router, prefix="/api/v1/threads", tags=["threads"])
api_router.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
