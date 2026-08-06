"""水利专家咨询 Agent 图

类似 EduAgent 的通用问答 Agent，用于回答水利行业的通用问题。

"""

from langgraph.graph import END, START, StateGraph

from backend.agents.water_expert.nodes import (
    classify_query_node,
    generate_direct_node,
    generate_with_context_node,
    save_memory_node,
)
from backend.agents.water_expert.state import WaterExpertState
from backend.core.logger import get_logger

logger = get_logger(__name__)


def _route_by_query_type(state: WaterExpertState) -> str:
    """根据查询类型路由（容错：未知类型一律兜底 GENERAL，绝不 KeyError 崩溃）"""
    qt = state.get("query_type", "GENERAL").upper()
    valid = ("GENERAL", "CONTEXT")
    if qt not in valid:
        logger.warning("Unknown query_type %r, defaulting to GENERAL", qt)
        return "GENERAL"
    return qt


def build_water_expert_graph():
    """构建水利专家咨询 Agent 图"""
    builder = StateGraph(WaterExpertState)

    # 注册节点
    builder.add_node("classify_query", classify_query_node)
    builder.add_node("generate_direct", generate_direct_node)
    builder.add_node("generate_with_context", generate_with_context_node)
    builder.add_node("save_memory", save_memory_node)

    # 入口
    builder.add_edge(START, "classify_query")

    # 条件边：根据查询类型路由
    builder.add_conditional_edges(
        "classify_query",
        _route_by_query_type,
        {
            "GENERAL": "generate_direct",
            "CONTEXT": "generate_with_context",
        },
    )

    # 固定边
    builder.add_edge("generate_direct", "save_memory")
    builder.add_edge("generate_with_context", "save_memory")
    builder.add_edge("save_memory", END)

    # 编译
    # 不用 checkpointer：多轮记忆由 chat.py / unified_chat 从 DB 加载历史注入，
    # 叠加 MemorySaver 会导致历史消息每轮重复（见 knowledge_qa/graph.py 说明）。
    return builder.compile()
