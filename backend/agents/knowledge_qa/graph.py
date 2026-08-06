"""StateGraph 编排、条件边、终止

知识库问答 Agent 图。

"""

from langgraph.graph import END, START, StateGraph

from backend.agents.knowledge_qa.nodes import (
    classify_query_node,
    enqueue_pending_node,
    generate_direct_node,
    generate_rag_node,
    hyde_generate_node,
    multi_query_rewrite_node,
    rerank_node,
    retrieve_node,
    save_memory_node,
)
from backend.agents.knowledge_qa.state import KnowledgeQAState
from backend.core.logger import get_logger

logger = get_logger(__name__)


def _route_by_query_type(state: KnowledgeQAState) -> str:
    """根据查询类型路由（容错：未知类型一律兜底到 GENERAL，绝不 KeyError 崩溃）

    classify_query_node 写入的是 core 分类器的输出（GENERAL / SPECIALIZED）。
    SPECIALIZED（专业问题，需 RAG 检索）显式映射到 retrieve；其余未知值兜底，
    保证未来分类器变更也不会让 graph 崩溃。
    """
    qt = state.get("query_type", "PRECISE").upper()
    valid = ("PRECISE", "VAGUE", "BROAD", "GENERAL", "SPECIALIZED")
    if qt not in valid:
        logger.warning("Unknown query_type %r, defaulting to GENERAL", qt)
        return "GENERAL"
    return qt


def _route_by_confidence(state: KnowledgeQAState) -> str:
    """根据置信度路由：HIGH/MEDIUM（有证据）→ RAG 生成；LOW（证据弱）→ 直接生成。"""
    if state.get("is_high_confidence", False):
        return "high"
    return "low"


def build_knowledge_qa_graph():
    """构建知识库问答图"""
    builder = StateGraph(KnowledgeQAState)

    # 注册节点
    builder.add_node("classify_query", classify_query_node)
    builder.add_node("hyde_generate", hyde_generate_node)
    builder.add_node("multi_query_rewrite", multi_query_rewrite_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("rerank", rerank_node)
    builder.add_node("generate_rag", generate_rag_node)
    builder.add_node("generate_direct", generate_direct_node)
    builder.add_node("enqueue_pending", enqueue_pending_node)
    builder.add_node("save_memory", save_memory_node)

    # 入口
    builder.add_edge(START, "classify_query")

    # 条件边①：查询类型路由
    builder.add_conditional_edges(
        "classify_query",
        _route_by_query_type,
        {
            "PRECISE": "retrieve",
            "VAGUE": "hyde_generate",
            "BROAD": "multi_query_rewrite",
            "GENERAL": "generate_direct",
            "SPECIALIZED": "retrieve",  # 专业问题 → 走 RAG 检索（而非 KeyError 崩溃）
        },
    )

    # VAGUE / BROAD 预处理完成后汇入 retrieve
    builder.add_edge("hyde_generate", "retrieve")
    builder.add_edge("multi_query_rewrite", "retrieve")

    # retrieve → rerank
    builder.add_edge("retrieve", "rerank")

    # 条件边②：置信度路由
    builder.add_conditional_edges(
        "rerank",
        _route_by_confidence,
        {
            "high": "generate_rag",
            "low": "generate_direct",
        },
    )

    # 固定边：各生成节点 → 收尾节点
    builder.add_edge("generate_rag", "save_memory")
    builder.add_edge("generate_direct", "enqueue_pending")
    builder.add_edge("enqueue_pending", "save_memory")
    builder.add_edge("save_memory", END)

    # 编译
    # 不用 checkpointer：多轮记忆由 chat.py 从 DB 加载历史并注入初始 state。
    # 若再叠加 MemorySaver，orchestrator 注入的 DB 历史 + checkpointer 内的历史会重复，
    # 每轮消息翻倍、浪费 token 且把重复内容当新信息。
    return builder.compile()
