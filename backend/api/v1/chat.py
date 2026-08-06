"""POST /api/v1/chat/stream (SSE)

对话接口：调 orchestrator + SSE 流式输出。


事件流契约（与前端 src/api/chat.ts 对齐）：
    event: start      data: { thread_id }
    event: status     data: { phase: <str> }
    event: token      data: { delta: <str> }    （按字符切片，伪流式）
    event: citation   data: { index, source_id, source_name, page, content }
    event: citation_verdict  data: { items: [{index, verified}] }   （G3.2 答案生成后回传核实结果）
    event: done       data: { message_id }
    event: error      data: { code, message }
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.config import settings
from backend.core.budget import budget_manager
from backend.core.logger import get_logger
from backend.core.orchestrator import AgentRequest, AgentType, get_orchestrator
from backend.core.rate_limit import check_rate_limit
from backend.core.risk import high_risk_warning, is_high_risk
from backend.core.sse import (
    create_citation_event,
    create_citation_verdict_event,
    create_done_event,
    create_error_event,
    create_start_event,
    create_status_event,
    create_token_event,
    create_warning_event,
)
from backend.db.connection import close_db, get_connection
from backend.rag.citation import citation_checker
from backend.rag.retriever import retrieve as hybrid_retrieve

logger = get_logger(__name__)
router = APIRouter()

# SSE 心跳间隔（秒）：防代理空闲断连；测试用 monkeypatch 调小以加速"断开即取消"用例
_KEEPALIVE_S = 15


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, description="用户问题")
    thread_id: str = Field(default="default", description="会话线程 ID")


async def _save_user_message(thread_id: str, content: str, user_id: str = "local_user") -> str:
    """持久化用户消息；返回 message_id。"""
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


async def _save_assistant_message(
    thread_id: str,
    content: str,
    citations: list[dict],
    agent_type: str,
    parent_id: str,
    user_id: str = "local_user",
) -> str:
    """持久化助手消息；返回 message_id。"""
    import json

    db = await get_connection()
    try:
        mid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO messages "
            "(message_id, thread_id, role, content, citations_json, agent_type, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                mid,
                thread_id,
                "assistant",
                content,
                json.dumps(citations, ensure_ascii=False),
                agent_type,
                user_id,
            ),
        )
        await db.commit()
        return mid
    finally:
        await close_db(db)


async def _load_history(thread_id: str, limit: int = 6, user_id: str | None = None) -> list:
    """加载该会话最近 limit 条历史消息，构造 langchain 消息列表（多轮记忆）。

    返回 [HumanMessage, AIMessage, ...]，供 orchestrator 注入初始 state 的 messages，
    使 generate 节点能携带上下文回答（多轮记忆）。
    数据隔离：仅加载属于当前用户的历史。
    """
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

    # 逆序成时间正序（最新在后）
    msgs: list = []
    for role, content in reversed(rows):
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs


async def _fetch_top_evidence(query: str, top_k: int = 3, user_id: str | None = None) -> list[dict]:
    """用统一混合检索（dense + sparse）拿 top-k 证据，转成 citation 事件结构。

    走统一检索入口 retriever.retrieve()：
      - 长查询靠 BM25 trigram 命中
      - 短查询（如"水库"）靠 dense embedding 兜底（trigram 不索引 2 字词）
    数据隔离：仅检索当前用户拥有的文档。
    """
    try:
        results = await hybrid_retrieve(query, top_k=top_k, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("hybrid retrieve failed: %s", e)
        return []
    # 批量取文档标题（source_name 更友好）
    doc_titles = await _fetch_doc_titles([r.document_id for r in results])
    citations: list[dict] = []
    for i, r in enumerate(results, start=1):
        citations.append(
            {
                "index": i,
                "source_id": r.chunk_id,
                "source_name": doc_titles.get(r.document_id, (r.document_id or "")[:12]),
                "page": r.page,  # sparse 命中时有页码
                "content": (r.content or "")[:300],
            }
        )
    return citations


async def _fetch_doc_titles(document_ids: list[str]) -> dict[str, str]:
    """批量查 documents 表拿标题（去重后的 id → title）。"""
    unique = list(dict.fromkeys(document_ids))
    if not unique:
        return {}
    db = await get_connection()
    try:
        placeholders = ",".join("?" * len(unique))
        # 动态部分只有占位符序列，值全部参数绑定 → 无注入面
        async with db.execute(
            f"SELECT document_id, document_title FROM documents "  # noqa: S608
            f"WHERE document_id IN ({placeholders})",
            unique,
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        await close_db(db)


def _split_tokens(text: str, chunk: int = 8) -> list[str]:
    """把字符串切成 8 字符一组的伪流式 token 序列。"""
    return [text[i : i + chunk] for i in range(0, len(text), chunk)] or [""]


async def _chat_stream(query: str, thread_id: str, user: CurrentUser) -> AsyncIterator[str]:
    """SSE 事件流生成器：orchestrator.handle(knowledge_qa) → token 流式 yield。

    P0 简化：
        - agent_type 固定 knowledge_qa（water_expert / document_analysis 走 unified_chat）
        - token 输出采用字符切片（等接入 LLM astream 后改为真流式）
        - 不阻塞 SSE：orchestrator 完成后一次性切片 yield
    数据隔离：会话/历史/检索均限定在当前用户。
    抽出为模块级生成器便于单测：anext() 推进事件、aclose() 模拟客户端断开（G9.3）。
    """
    orch = get_orchestrator()

    # 1. start
    yield create_start_event(thread_id).format()

    # 2. 加载多轮历史（保存当前消息前，历史不含本轮 query）
    history = await _load_history(thread_id, limit=6, user_id=user.user_id)

    # 3. 持久化用户消息（外层不入 message_id，由前端展示）
    try:
        await _save_user_message(thread_id, query, user_id=user.user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("save user msg failed: %s", e)

    # 4. status: retrieving
    yield create_status_event("retrieving").format()

    # 5. 取 top 证据（与 orchestrator 内部检索独立；这里仅用于 citation 事件展示）
    citations = await _fetch_top_evidence(query, top_k=3, user_id=user.user_id)
    for c in citations:
        yield create_citation_event(c).format()

    # 6. status: generating → 调 orchestrator（带多轮记忆历史，不含当前轮）
    yield create_status_event("generating").format()

    # 真流式：TokenStreamHandler 把 LLM 逐 token 推入队列，下方循环并发 drain；
    # UsageCollector 同链收集 token 用量，请求结束落库（G3.1 成本记账）
    from backend.core.token_stream import TokenStreamHandler
    from backend.core.usage import UsageCollector

    stream_q: asyncio.Queue = asyncio.Queue()
    stream_handler = TokenStreamHandler(stream_q)
    usage_collector = UsageCollector()

    agent_req = AgentRequest(
        user_id=user.user_id,
        session_id=thread_id,
        agent_type=AgentType.KNOWLEDGE_QA,
        user_message=query,
        context={
            "history": history,
            "llm_callbacks": [stream_handler, usage_collector],
        },
    )

    # orchestrator 在独立 task 中运行：
    #   - 每 0.2s drain 队列 → 逐 token 真流式推送
    #   - 每 15s 发一次心跳，防代理空闲断连
    task = asyncio.create_task(orch.handle(agent_req))
    last_ping = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.2)
            while not stream_q.empty():
                _kind, payload = stream_q.get_nowait()
                if _kind == "token":
                    yield create_token_event(payload).format()
            now = time.monotonic()
            if now - last_ping >= _KEEPALIVE_S:
                yield ": keep-alive\n\n"
                last_ping = now
            if task in done:
                while not stream_q.empty():
                    _kind, payload = stream_q.get_nowait()
                    if _kind == "token":
                        yield create_token_event(payload).format()
                response = task.result()
                break
    except Exception as e:
        if not task.done():
            task.cancel()
        logger.exception("orchestrator crashed")
        yield create_error_event("ORCHESTRATOR_ERROR", str(e)).format()
        return
    finally:
        # G9.3：客户端断开（GeneratorExit，BaseException 不被 except 捕获）或任何
        # 异常退出，统一取消并回收后台任务，避免孤儿任务继续占用连接、消耗 LLM 额度
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # 完成一次 orchestrator 调用即计入当日额度（每用户）
    budget_manager.record_call(user.user_id, settings.LLM_MODEL)
    # G3.1: token 用量落库（flush 内部已兜底，失败不影响回复）
    await usage_collector.flush(user.user_id, agent_type="knowledge_qa")

    if not response.success:
        yield create_error_event(response.error_msg or "AGENT_ERROR", "agent failed").format()
        return

    # 真流式：token 已在上方循环逐条推送；此处取完整答案用于落库
    answer = response.content or ""

    # G3.2 引用核实：答案生成后对已展示的引用做词汇覆盖校验（防幻觉信号）
    #   - 落库的 citations 带 verified 标记，历史回放时前端可直接展示
    #   - 额外推送 citation_verdict 事件，让当前会话的引用面板即时更新
    citations = citation_checker.verify_citation(answer, citations)
    yield create_citation_verdict_event(
        [{"index": c["index"], "verified": c["verified"]} for c in citations]
    ).format()

    # 6.5 高风险提示：问题涉及防汛调度/工程安全/法规合规时附人工复核提醒
    if is_high_risk(query):
        yield create_warning_event(high_risk_warning()).format()

    # 7. done：写助手消息 + 真实 message_id
    try:
        mid = await _save_assistant_message(
            thread_id,
            answer,
            citations,
            "knowledge_qa",
            parent_id="",
            user_id=user.user_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("save assistant msg failed: %s", e)
        mid = str(uuid.uuid4())
    yield create_done_event(mid).format()


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(check_rate_limit),
) -> StreamingResponse:
    """对话流 SSE 端点：预算拦截 + 流式响应（事件流逻辑见 `_chat_stream`）。"""
    # 每日调用限额（DAILY_CALL_LIMIT，每用户）：超限直接 429，不再消耗云额度
    budget_manager.check_budget(user.user_id)
    return StreamingResponse(
        _chat_stream(req.query, req.thread_id, user),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
