"""HTTP 客户端

httpx.AsyncClient 封装。

"""

import httpx

from backend.config import settings


def create_http_client() -> httpx.AsyncClient:
    """创建 HTTP 客户端"""
    return httpx.AsyncClient(
        trust_env=settings.HTTP_TRUST_ENV,
        timeout=httpx.Timeout(settings.LLM_TIMEOUT_S),
    )
