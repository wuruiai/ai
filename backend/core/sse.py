"""SSE 事件封装

Server-Sent Events 工具。

Reference: §9.7
"""

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class SSEEvent:
    """SSE 事件"""

    event: str
    data: Any
    id: str | None = None

    def format(self) -> str:
        """格式化为 SSE 格式（按 SSE 规范使用 \\r\\n 分隔）"""
        lines = []
        if self.id:
            lines.append(f"id: {self.id}")
        lines.append(f"event: {self.event}")
        lines.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
        lines.append("")
        lines.append("")  # 事件分隔空行
        return "\r\n".join(lines)


def create_start_event(thread_id: str) -> SSEEvent:
    """创建开始事件"""
    return SSEEvent(event="start", data={"thread_id": thread_id})


def create_status_event(status: str) -> SSEEvent:
    """创建状态事件"""
    return SSEEvent(event="status", data={"status": status})


def create_token_event(token: str) -> SSEEvent:
    """创建 token 事件（字段名 delta 与前端 TokenEvent 对齐）"""
    return SSEEvent(event="token", data={"delta": token})


def create_citation_event(citation: dict) -> SSEEvent:
    """创建引用事件"""
    return SSEEvent(event="citation", data=citation)


def create_done_event(message_id: str) -> SSEEvent:
    """创建完成事件"""
    return SSEEvent(event="done", data={"message_id": message_id})


def create_error_event(code: str, message: str) -> SSEEvent:
    """创建错误事件"""
    return SSEEvent(event="error", data={"code": code, "message": message})


def create_warning_event(message: str) -> SSEEvent:
    """创建高风险提示事件"""
    return SSEEvent(event="warning", data={"message": message})
