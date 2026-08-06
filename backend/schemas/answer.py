"""答案 JSON 结构

答案数据模型。

Reference: §7.1
"""

from pydantic import BaseModel


class Citation(BaseModel):
    """引用"""

    index: int
    source_id: str
    source_name: str
    page: int
    content: str
    score: float


class Answer(BaseModel):
    """答案"""

    message_id: str
    thread_id: str
    content: str
    citations: list[Citation]
    status: str = "done"
    is_reliable: bool = True
    refusal_reason: str | None = None
