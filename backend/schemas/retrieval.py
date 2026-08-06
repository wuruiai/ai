"""RetrievalRequest / Evidence

检索数据模型。

Reference: §6.4
"""

from pydantic import BaseModel


class RetrievalRequest(BaseModel):
    """检索请求"""

    query: str
    top_k: int | None = None
    document_id: str | None = None


class Evidence(BaseModel):
    """证据"""

    chunk_id: str
    content: str
    document_id: str
    page: int
    score: float
    source: str  # "dense" or "sparse"


class RetrievalResult(BaseModel):
    """检索结果"""

    query: str
    evidence: list[Evidence]
    total: int


class RetrievalTiming(BaseModel):
    """检索耗时"""

    dense_ms: float
    sparse_ms: float
    rerank_ms: float
    total_ms: float
