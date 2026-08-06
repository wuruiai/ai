"""统一聊天接口

支持所有 Agent 的统一入口。


事件契约：
    event: start   data: { thread_id }
    event: status  data: { phase }
    event: token   data: { delta }       （伪流式：按字符切片）
    event: done    data: { message_id }
    event: error   data: { code, message }
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.config import settings
from backend.core.budget import budget_manager
from backend.core.logger import get_logger
from backend.core.orchestrator import (
    AgentRequest,
    AgentResponse,
    AgentType,
    get_orchestrator,
)
from backend.core.rate_limit import check_rate_limit
from backend.core.sse import (
    create_done_event,
    create_error_event,
    create_start_event,
    create_status_event,
    create_token_event,
)
from backend.core.usage import UsageCollector
from backend.db.connection import close_db, get_connection

logger = get_logger(__name__)
router = APIRouter()


class UnifiedChatRequest(BaseModel):
    """统一聊天请求"""

    message: str = Field(..., min_length=1, description="用户消息")
    agent_type: AgentType = Field(
        default=AgentType.KNOWLEDGE_QA,
        description="knowledge_qa / document_analysis / water_expert",
    )
    session_id: str = Field(default="default", description="会话 ID")
    context: dict = Field(default_factory=dict)
    pipeline_mode: bool = False
    pipeline_key: str | None = None


def _split_tokens(text: str, chunk: int = 12) -> list[str]:
    """把字符串切成 12 字符一组的伪流式 token 序列。"""
    return [text[i : i + chunk] for i in range(0, len(text), chunk)] or [""]


async def _save_user_message(thread_id: str, content: str, user_id: str = "local_user") -> str:
    db = await get_connection()
    try:
        mid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO messages (message_id, thread_id, role, content, user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, thread_id, "user", content, user_id),
        )
        await db.commit()
        return mid
    finally:
        await close_db(db)


async def _load_history(thread_id: str, limit: int = 6, user_id: str | None = None) -> list:
    """加载会话历史消息（多轮记忆），与 chat.py 对齐。数据隔离：仅当前用户。"""
    from langchain_core.messages import AIMessage, HumanMessage

    db = await get_connection()
    try:
        # rowid 二级排序：datetime('now') 秒级精度，同秒消息靠 rowid 保证插入顺序。
        # 值全部参数绑定；user_id 过滤即数据隔离。
        if user_id:
            sql = (
                "SELECT role, content FROM messages "
                "WHERE thread_id=? AND user_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?"
            )
            params: list = [thread_id, user_id, limit]
        else:
            sql = (
                "SELECT role, content FROM messages "
                "WHERE thread_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?"
            )
            params = [thread_id, limit]
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    except Exception:  # noqa: BLE001
        return []
    finally:
        await close_db(db)

    msgs: list = []
    for role, content in reversed(rows):
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs


async def _save_assistant_message(
    thread_id: str, content: str, agent_type: str, user_id: str = "local_user"
) -> str:
    db = await get_connection()
    try:
        mid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO messages (message_id, thread_id, role, content, agent_type, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, thread_id, "assistant", content, agent_type, user_id),
        )
        await db.commit()
        return mid
    finally:
        await close_db(db)


@router.post("/stream")
async def unified_chat_stream(
    request: UnifiedChatRequest,
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(check_rate_limit),
) -> StreamingResponse:
    """统一聊天流。"""
    # 每日调用限额（DAILY_CALL_LIMIT，每用户）：超限直接 429，不再消耗云额度
    budget_manager.check_budget(user.user_id)
    orchestrator = get_orchestrator()

    async def generate() -> AsyncIterator[str]:
        yield create_start_event(request.session_id).format()
        yield create_status_event("processing").format()

        # 多轮记忆：保存当前消息前加载历史（不含当前轮）
        history = await _load_history(request.session_id, limit=6, user_id=user.user_id)

        # G3.1 用量记账：与 chat/stream 一致，经 llm_callbacks 收集 token，请求结束落库
        usage_collector = UsageCollector()

        agent_request = AgentRequest(
            user_id=user.user_id,
            session_id=request.session_id,
            agent_type=request.agent_type,
            user_message=request.message,
            context={
                **request.context,
                "pipeline_key": request.pipeline_key,
                "history": history,
                "llm_callbacks": [usage_collector],
            },
            pipeline_mode=request.pipeline_mode,
        )

        # 持久化用户消息
        try:
            await _save_user_message(request.session_id, request.message, user_id=user.user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("save user msg failed: %s", e)

        try:
            response = await orchestrator.handle(agent_request)
        except Exception as e:
            logger.exception("orchestrator crashed")
            yield create_error_event("ORCHESTRATOR_ERROR", str(e)).format()
            return
        finally:
            # G3.1：token 用量落库（flush 内部兜底，失败/异常不影响回复）
            await usage_collector.flush(user.user_id, agent_type=request.agent_type)

        # 完成一次 orchestrator 调用即计入当日额度（每用户）
        budget_manager.record_call(user.user_id, settings.LLM_MODEL)

        if not response.success:
            yield create_error_event(response.error_msg or "AGENT_ERROR", "agent failed").format()
            return

        # 伪流式 token
        for tok in _split_tokens(response.content or ""):
            yield create_token_event(tok).format()

        # done：真实 message_id
        try:
            mid = await _save_assistant_message(
                request.session_id,
                response.content or "",
                request.agent_type,
                user_id=user.user_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("save assistant msg failed: %s", e)
            mid = str(uuid.uuid4())
        yield create_done_event(mid).format()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/")
async def unified_chat(
    request: UnifiedChatRequest,
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(check_rate_limit),
):
    """统一聊天（非流式）。"""
    # 每日调用限额（DAILY_CALL_LIMIT，每用户）：超限直接 429，不再消耗云额度
    budget_manager.check_budget(user.user_id)

    orchestrator = get_orchestrator()
    # 多轮记忆：与流式端点一致，加载历史注入 context（仅当前用户）
    history = await _load_history(request.session_id, limit=6, user_id=user.user_id)
    # G9.1 用量记账：与流式端点一致，经 llm_callbacks 收集 token，请求结束落库
    usage_collector = UsageCollector()
    agent_request = AgentRequest(
        user_id=user.user_id,
        session_id=request.session_id,
        agent_type=request.agent_type,
        user_message=request.message,
        context={
            **request.context,
            "pipeline_key": request.pipeline_key,
            "history": history,
            "llm_callbacks": [usage_collector],
        },
        pipeline_mode=request.pipeline_mode,
    )
    try:
        response = await orchestrator.handle(agent_request)
    except Exception as e:
        logger.exception("orchestrator crashed")
        response = AgentResponse(
            success=False,
            agent_type=request.agent_type,
            content="系统处理请求时遇到问题，请稍后再试。",
            error_msg=str(e),
        )
    finally:
        # G9.1：token 用量落库（flush 内部兜底，失败/异常不影响回复）
        await usage_collector.flush(user.user_id, agent_type=request.agent_type)

    # 完成一次 orchestrator 调用即计入当日额度（每用户）
    budget_manager.record_call(user.user_id, settings.LLM_MODEL)
    return response
