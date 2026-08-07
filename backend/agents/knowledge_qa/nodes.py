"""知识库问答 Agent 节点

包含 HyDE、多查询重写、置信度路由等高级功能。

"""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.knowledge_qa.state import KnowledgeQAState
from backend.core.confidence_router import ConfidenceLevel, get_confidence_router
from backend.core.logger import get_logger
from backend.core.model_factory import ModelFactory
from backend.core.query_classifier import get_query_classifier
from backend.core.token_stream import usage_only_callbacks
from backend.rag.hyde import get_hyde_generator
from backend.rag.multi_query import get_multi_query_rewriter
from backend.rag.reranker import rerank
from backend.rag.retriever import retrieve

logger = get_logger(__name__)


async def classify_query_node(state: KnowledgeQAState) -> dict[str, Any]:
    """分类查询节点"""
    messages = state.get("messages", [])
    if not messages:
        return {"query_type": "PRECISE"}

    last_message = messages[-1]
    query = last_message.content if hasattr(last_message, "content") else str(last_message)

    # 使用查询分类器（G10.7 M1：带用量回调，辅助 LLM 的 token 也计入 llm_usage）
    # S2 流式泄漏：辅助 LLM 只挂用量链——usage_only_callbacks() 剔除 TokenStreamHandler，
    # 否则分类器 JSON 会逐 token 泄漏到用户可见的 SSE 流。
    classifier = get_query_classifier()
    query_type, _ = await classifier.classify(
        query, callbacks=usage_only_callbacks(state.get("llm_callbacks"))
    )

    # 分类器返回 GENERAL / SPECIALIZED，需映射到 graph 路由能识别的类型：
    #   SPECIALIZED（专业问题）→ PRECISE（走 RAG 检索）
    #   GENERAL（通用问题）    → GENERAL（直接生成）
    # 修复：此前 SPECIALIZED 无对应路由会落到兜底 GENERAL，导致专业问题不走 RAG。
    if query_type.value.upper() == "SPECIALIZED":
        routed = "PRECISE"
    else:
        routed = "GENERAL"

    return {
        "query_type": routed,
        "original_query": query,
    }


async def hyde_generate_node(state: KnowledgeQAState) -> dict[str, Any]:
    """HyDE 生成节点（用于模糊查询）"""
    query = state.get("original_query", "")

    hyde_gen = get_hyde_generator()
    # S2 流式泄漏：辅助 LLM 只挂用量链（剔除 TokenStreamHandler），见 usage_only_callbacks
    hypothetical_doc = await hyde_gen.generate(
        query, callbacks=usage_only_callbacks(state.get("llm_callbacks"))
    )

    return {
        "hypothetical_doc": hypothetical_doc,
        "queries": [hypothetical_doc],  # 用假设性文档作为查询
        "llm_call_count": state.get("llm_call_count", 0) + 1,
    }


async def multi_query_rewrite_node(state: KnowledgeQAState) -> dict[str, Any]:
    """多查询重写节点（用于宽泛查询）"""
    query = state.get("original_query", "")

    rewriter = get_multi_query_rewriter()
    # S2 流式泄漏：辅助 LLM 只挂用量链（剔除 TokenStreamHandler），见 usage_only_callbacks
    queries = await rewriter.rewrite(
        query, callbacks=usage_only_callbacks(state.get("llm_callbacks"))
    )

    return {
        "queries": queries,
        "llm_call_count": state.get("llm_call_count", 0) + 1,
    }


async def retrieve_node(state: KnowledgeQAState) -> dict[str, Any]:
    """检索节点"""
    queries = state.get("queries", [])
    if not queries:
        queries = [state.get("original_query", "")]

    # 执行检索（数据隔离：只检索当前用户拥有的文档）
    all_evidence = []
    current_user = state.get("user_id")
    for query in queries:
        results = await retrieve(query, user_id=current_user)
        for r in results:
            all_evidence.append(
                {
                    "chunk_id": r.chunk_id,
                    "content": r.content,
                    "document_id": r.document_id,
                    "score": r.score,
                    "source": r.source,
                    "page": r.page,  # G10.20：保留页码，随证据透传到 citation
                }
            )

    # 去重
    seen = set()
    unique_evidence = []
    for e in all_evidence:
        if e["chunk_id"] not in seen:
            seen.add(e["chunk_id"])
            unique_evidence.append(e)

    # 评估置信度
    confidence_router = get_confidence_router()
    confidence_level, avg_score = confidence_router.evaluate(unique_evidence)

    return {
        "evidence": unique_evidence,
        # HIGH 与 MEDIUM 都进 RAG 生成：融合分数被 DENSE_WEIGHT=0.7 封顶，
        # 单一路径命中时最高只有 0.7×归一化，阈值 0.7 实际不可达；
        # 若仅 HIGH 走 generate_rag，检索证据会被丢弃、答案退化成无 RAG 直答。
        "is_high_confidence": confidence_level in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM),
        "confidence_score": avg_score,
        "step_count": state.get("step_count", 0) + 1,
    }


async def rerank_node(state: KnowledgeQAState) -> dict[str, Any]:
    """重排序节点"""
    evidence = state.get("evidence", [])
    query = state.get("original_query", "")

    if not evidence:
        return {"reranked_evidence": []}

    # 提取文档内容
    documents = [e["content"] for e in evidence]

    # 执行重排序（失败降级：直接用原始检索结果，即"Rerank 失败可降级"策略）
    fallback_used = False
    try:
        reranked = await rerank(query, documents)
    except Exception as e:  # noqa: BLE001
        logger.warning("rerank failed, degrading to raw retrieval: %s", e)
        fallback_used = True
        reranked = [{"index": i, "score": 0.0} for i in range(len(evidence))]

    # 构建重排序结果
    reranked_evidence = []
    for item in reranked:
        idx = item["index"]
        if idx < len(evidence):
            reranked_evidence.append(
                {
                    **evidence[idx],
                    "rerank_score": item.get("score", 0.0),
                }
            )

    # 降级时保持原始顺序（rerank 成功时已按新序返回）
    if not reranked_evidence and evidence:
        reranked_evidence = [{**e, "rerank_score": 0.0} for e in evidence[:5]]

    return {
        "reranked_evidence": reranked_evidence,
        "fallback_used": state.get("fallback_used", False) or fallback_used,
        "step_count": state.get("step_count", 0) + 1,
    }


async def generate_rag_node(state: KnowledgeQAState) -> dict[str, Any]:
    """RAG 生成节点（高置信度）"""
    llm = ModelFactory.create_llm(temperature=0.7, callbacks=state.get("llm_callbacks"))
    messages = state.get("messages", [])
    evidence = state.get("reranked_evidence", state.get("evidence", []))

    # G10.20 引用同源：citations 与喂给 LLM 的证据切片完全一致（同源同序）。
    # 此前 chat.py 独立 top-3 检索生成 citations，与 LLM 实际依据的 rerank top-8
    # 不是同一批 chunk，答案里的 [N] 与引用面板对不上（来源错配/误导）。
    cited = evidence[:8]

    # 构建证据文本
    evidence_text = "\n\n".join([f"[{i + 1}] {e['content']}" for i, e in enumerate(cited)])

    # 构建提示
    system_prompt = f"""你是水利行业知识问答助手。请基于以下证据回答用户问题。

证据：
{evidence_text}

要求：
1. 回答要准确、专业
2. 引用证据时标注来源 [1][2]
3. 如果证据不足，明确说明
4. 使用 Markdown 格式"""

    llm_messages = [("system", system_prompt)]
    for msg in messages:
        if isinstance(msg, HumanMessage):
            llm_messages.append(("human", msg.content))
        elif isinstance(msg, AIMessage):
            llm_messages.append(("ai", msg.content))

    try:
        response = await llm.ainvoke(llm_messages)
        answer = response.content

        # G10.20：citations 即 evidence_text 的第 1..N 条（index 与答案 [N] 一一对应）
        citations = [
            {
                "index": i + 1,
                "source_id": e.get("chunk_id", ""),
                "document_id": e.get("document_id", ""),
                "page": e.get("page"),
                "content": (e.get("content") or "")[:300],
            }
            for i, e in enumerate(cited)
        ]

        return {
            "messages": [AIMessage(content=answer)],
            "answer": answer,
            "citations": citations,
            "llm_call_count": state.get("llm_call_count", 0) + 1,
            "step_count": state.get("step_count", 0) + 1,
        }

    except Exception as e:  # noqa: BLE001 -- LLM 外部调用失败，返回用户友好兜底回答
        logger.error("RAG generation failed: %s", e)
        return {
            "messages": [AIMessage(content="抱歉，生成回答时遇到问题，请稍后重试。")],
            "fallback_used": True,
        }


async def generate_direct_node(state: KnowledgeQAState) -> dict[str, Any]:
    """直接生成节点（低置信度，无 RAG）"""
    llm = ModelFactory.create_llm(temperature=0.7, callbacks=state.get("llm_callbacks"))
    messages = state.get("messages", [])

    system_prompt = """你是水利行业专家助手。请直接回答用户的问题。
如果不确定，请如实告知。"""

    llm_messages = [("system", system_prompt)]
    for msg in messages:
        if isinstance(msg, HumanMessage):
            llm_messages.append(("human", msg.content))
        elif isinstance(msg, AIMessage):
            llm_messages.append(("ai", msg.content))

    try:
        response = await llm.ainvoke(llm_messages)
        answer = response.content

        # 直答是低置信度/通用问题的正常路径，不算"降级"，不再置 fallback_used=True
        return {
            "messages": [AIMessage(content=answer)],
            "answer": answer,
            "llm_call_count": state.get("llm_call_count", 0) + 1,
        }

    except Exception as e:  # noqa: BLE001 -- LLM 外部调用失败，返回用户友好兜底回答
        logger.error("Direct generation failed: %s", e)
        return {
            "messages": [AIMessage(content="抱歉，生成回答时遇到问题，请稍后重试。")],
            "fallback_used": True,
        }


async def enqueue_pending_node(state: KnowledgeQAState) -> dict[str, Any]:
    """入队待处理节点（用于需要人工审核的情况）"""
    # TODO: 实现入队逻辑
    return {
        "step_count": state.get("step_count", 0) + 1,
    }


async def save_memory_node(state: KnowledgeQAState) -> dict[str, Any]:
    """保存记忆节点"""
    # TODO: 实现记忆保存逻辑
    return {}
