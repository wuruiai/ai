"""DashScope Rerank 精排

重排序器。

"""

from backend.config import settings
from backend.core.http_client import create_http_client


async def rerank(
    query: str,
    documents: list[str],
    top_k: int | None = None,
) -> list[dict]:
    """重排序"""
    top_k = top_k or settings.RERANK_TOP_K

    async with create_http_client() as client:
        response = await client.post(
            settings.DASHSCOPE_RERANK_URL,
            headers={
                "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.RERANK_MODEL,
                "input": {"query": query, "documents": documents},
                "parameters": {"top_n": top_k},
            },
        )
        # 非 2xx 抛异常（403=账号未开通权限，由调用方决定降级）
        response.raise_for_status()
        result = response.json()

    # 解析结果
    reranked = []
    for item in result.get("output", {}).get("results", []):
        reranked.append(
            {
                "index": item["index"],
                "score": item["relevance_score"],
            }
        )

    return reranked
