"""引用结构

引用数据模型。

"""

from pydantic import BaseModel


class CitationRequest(BaseModel):
    """引用请求"""

    chunk_id: str
    content: str
    document_id: str
    page: int


class CitationResponse(BaseModel):
    """引用响应"""

    index: int
    source_id: str
    source_name: str
    page: int
    content: str
    score: float
