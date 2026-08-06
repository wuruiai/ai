"""独立图编排

指定文档分析 Agent 图。

Reference: §8.2
"""

from langgraph.graph import END, StateGraph

from backend.agents.document_analysis.nodes import (
    analyze_content,
    extract_structure,
    finalize,
    generate_summary,
    load_document,
)
from backend.agents.document_analysis.state import DocumentAnalysisState


def create_document_analysis_graph() -> StateGraph:
    """创建文档分析图"""
    graph = StateGraph(DocumentAnalysisState)

    # 添加节点
    graph.add_node("load_document", load_document)
    graph.add_node("extract_structure", extract_structure)
    graph.add_node("analyze_content", analyze_content)
    graph.add_node("generate_summary", generate_summary)
    graph.add_node("finalize", finalize)

    # 设置入口
    graph.set_entry_point("load_document")

    # 添加边
    graph.add_edge("load_document", "extract_structure")
    graph.add_edge("extract_structure", "analyze_content")
    graph.add_edge("analyze_content", "generate_summary")
    graph.add_edge("generate_summary", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
