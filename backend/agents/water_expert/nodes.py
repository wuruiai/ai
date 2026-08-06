"""水利专家咨询 Agent 节点"""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.water_expert.state import WaterExpertState
from backend.core.logger import get_logger
from backend.core.model_factory import ModelFactory

logger = get_logger(__name__)


async def classify_query_node(state: WaterExpertState) -> dict[str, Any]:
    """分类查询"""
    messages = state.get("messages", [])
    if not messages:
        return {"query_type": "GENERAL"}

    last_message = messages[-1]
    query = last_message.content if hasattr(last_message, "content") else str(last_message)

    # 简单分类：如果有上下文相关的关键词，认为需要上下文
    context_keywords = ["文档", "报告", "规范", "标准", "上面", "之前", "刚才"]
    has_context = any(kw in query for kw in context_keywords)

    return {
        "query_type": "CONTEXT" if has_context else "GENERAL",
        "original_query": query,
    }


async def generate_direct_node(state: WaterExpertState) -> dict[str, Any]:
    """直接生成回答（通用问题）"""
    llm = ModelFactory.create_llm(temperature=0.7)
    messages = state.get("messages", [])

    # 构建系统提示
    system_prompt = """你是水利行业专家助手。请用专业、准确的语言回答用户的问题。
如果问题不属于水利领域，请礼貌地说明你的专业范围。"""

    # 构建消息列表
    llm_messages = [("system", system_prompt)]
    for msg in messages:
        if isinstance(msg, HumanMessage):
            llm_messages.append(("human", msg.content))
        elif isinstance(msg, AIMessage):
            llm_messages.append(("ai", msg.content))

    try:
        response = await llm.ainvoke(llm_messages)
        answer = response.content

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


async def generate_with_context_node(state: WaterExpertState) -> dict[str, Any]:
    """带上下文生成回答"""
    llm = ModelFactory.create_llm(temperature=0.7)
    messages = state.get("messages", [])

    # 构建系统提示
    system_prompt = """你是水利行业专家助手。请根据对话历史和用户当前的问题，提供专业、准确的回答。
注意保持对话的连贯性，引用之前提到的内容时要自然。"""

    # 构建消息列表
    llm_messages = [("system", system_prompt)]
    for msg in messages:
        if isinstance(msg, HumanMessage):
            llm_messages.append(("human", msg.content))
        elif isinstance(msg, AIMessage):
            llm_messages.append(("ai", msg.content))

    try:
        response = await llm.ainvoke(llm_messages)
        answer = response.content

        return {
            "messages": [AIMessage(content=answer)],
            "answer": answer,
            "llm_call_count": state.get("llm_call_count", 0) + 1,
        }

    except Exception as e:  # noqa: BLE001 -- LLM 外部调用失败，返回用户友好兜底回答
        logger.error("Context generation failed: %s", e)
        return {
            "messages": [AIMessage(content="抱歉，生成回答时遇到问题，请稍后重试。")],
            "fallback_used": True,
        }


async def save_memory_node(state: WaterExpertState) -> dict[str, Any]:
    """保存记忆"""
    # TODO: 实现记忆保存逻辑
    return {}
