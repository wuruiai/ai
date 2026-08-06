"""水利专家咨询 Agent 状态"""

from typing import Any

from langgraph.graph import MessagesState


class WaterExpertState(MessagesState):
    """水利专家咨询状态"""

    # 输入
    student_id: str = ""
    tenant_id: str = "default"
    session_id: str = ""

    # 查询处理
    query_type: str = "GENERAL"  # GENERAL / CONTEXT
    original_query: str = ""

    # 生成结果
    answer: str = ""
    structured_output: dict[str, Any] | None = None

    # 记忆
    thread_id: str = ""
    summary: str = ""

    # 计数器
    step_count: int = 0
    llm_call_count: int = 0

    # 降级标记
    fallback_used: bool = False

    # 用量记账：由 chat.py / unified_chat 注入的 LLM token 回调（UsageCollector 等）
    llm_callbacks: Any = None
