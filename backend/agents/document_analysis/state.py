"""DocumentAnalysisState（独立定义）

指定文档分析 Agent 状态。

Reference: §8.3

继承 MessagesState（与其他 Agent 一致）：
    - LangGraph 会把 state 作为 dict 传入节点，节点内可用 state.get()
    - messages 列表由 MessagesState 提供 reducer，天然支持多消息
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import MessagesState


class DocumentAnalysisState(MessagesState):
    """文档分析状态"""

    # 输入
    document_id: str = ""
    query: str = ""

    # 中间状态（chunks 用 `_` 前缀避免与 LangGraph 冲突）
    document_loaded: bool = False
    structure: dict[str, Any] = {}
    analysis: dict[str, Any] = {}
    _chunks: list[dict] = []

    # 输出
    summary: str = ""
    key_points: list[str] = []
    status: str = "pending"
    structured_output: dict[str, Any] | None = None

    # 计数器
    step_count: int = 0
    llm_call_count: int = 0
