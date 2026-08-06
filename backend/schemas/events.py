"""SSE 事件类型定义

事件数据模型。

Reference: §9.7
"""

from pydantic import BaseModel


class StartEvent(BaseModel):
    """开始事件"""

    thread_id: str


class StatusEvent(BaseModel):
    """状态事件"""

    status: str
    message: str | None = None


class TokenEvent(BaseModel):
    """Token 事件"""

    token: str


class CitationEvent(BaseModel):
    """引用事件"""

    index: int
    source_id: str
    source_name: str
    page: int
    content: str


class DoneEvent(BaseModel):
    """完成事件"""

    message_id: str


class ErrorEvent(BaseModel):
    """错误事件"""

    code: str
    message: str
