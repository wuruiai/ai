"""Embedding 调用封装

向量化工具，直接调 DashScope 兼容的 OpenAI 协议端点。

Reference: §4.2

注：langchain_openai 1.1.10 的 OpenAIEmbeddings 在 list 文档场景会把 list
错误地序列化为 str，dashscope-compatible 端点会回 400 InvalidParameter。
因此绕开 LangChain 包装层，直接用 openai SDK 调 v1/embeddings。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from backend.config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
            # 偶发限流/超时自动重试（方案文档 §4.4）
            max_retries=max(2, settings.MAX_RETRIES),
            timeout=settings.LLM_TIMEOUT_S,
        )
    return _client


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """获取文本向量（批量）。空列表直接返回 []。"""
    if not texts:
        return []
    # 过滤空字符串，避免 dashscope 400
    cleaned = [t for t in texts if t and t.strip()]
    if not cleaned:
        return []
    # 空串无法 embedding：若混入空串，返回长度会与入参错位，导致
    # vector_store.add_documents 的 ids/documents/embeddings 不一致。
    # 显式报错而不是静默错位（调用方应先过滤空 chunk，如 ingestion_worker 已做）。
    if len(cleaned) != len(texts):
        raise ValueError("get_embeddings: texts 含空字符串，返回长度与入参错位；请先过滤空 chunk")
    client = _get_client()
    resp = await client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=cleaned,
        dimensions=settings.EMBEDDING_DIM,
    )
    return [item.embedding for item in resp.data]


async def get_embedding(text: str) -> list[float]:
    """获取单个文本向量。"""
    vecs = await get_embeddings([text])
    return vecs[0]
