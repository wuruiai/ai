"""KnowledgeQAState（全可序列化）

知识库问答 Agent 状态。

"""

from typing import Any

from langgraph.graph import MessagesState


class KnowledgeQAState(MessagesState):
    """知识库问答状态"""

    # 输入
    student_id: str = ""
    tenant_id: str = "default"
    session_id: str = ""

    # 查询处理
    query_type: str = "PRECISE"  # PRECISE / VAGUE / BROAD / GENERAL
    original_query: str = ""
    queries: list[str] = []  # 多查询重写结果
    hypothetical_doc: str = ""  # HyDE 生成的假设性文档

    # 检索结果
    evidence: list[dict[str, Any]] = []
    reranked_evidence: list[dict[str, Any]] = []
    is_high_confidence: bool = False
    confidence_score: float = 0.0

    # 生成结果
    answer: str = ""
    citations: list[dict[str, Any]] = []
    structured_output: dict[str, Any] | None = None

    # 记忆
    thread_id: str = ""
    summary: str = ""

    # 计数器（硬上限）
    step_count: int = 0
    revision_count: int = 0
    llm_call_count: int = 0

    # 降级标记
    fallback_used: bool = False

    # 真流式：由 chat.py 注入的 LLM token 回调（TokenStreamHandler + UsageCollector）。
    # 辅助 LLM（分类器/HyDE/多查询）经 usage_only_callbacks() 剔除 TokenStreamHandler，
    # 只留用量链（S2 防中间产物泄漏到 SSE）；最终答案生成节点用完整链流式输出。
    llm_callbacks: Any = None

    # Web 搜索
    enable_web_search: bool = False
    web_search_results: list[dict[str, Any]] = []
