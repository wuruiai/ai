"""聊天流式 API 测试（mock orchestrator + 模拟 token 回调）。"""

import asyncio
import threading

from fastapi.testclient import TestClient

from backend.api.v1 import chat as chat_api
from backend.core.orchestrator import AgentResponse, AgentType
from backend.main import app


class _FakeOrch:
    """模拟 orchestrator：读取注入的 llm_callbacks，模拟真流式逐 token 回调。"""

    def __init__(self):
        self.user_id = None

    async def handle(self, agent_req):
        self.user_id = agent_req.user_id
        cbs = agent_req.context.get("llm_callbacks") or []
        for token in list("测试回答内容"):
            for cb in cbs:
                cb.on_llm_new_token(token)
        return AgentResponse(
            success=True,
            agent_type=AgentType.KNOWLEDGE_QA,
            content="测试回答内容",
            fallback_used=False,
        )


def test_chat_stream_true_streaming(monkeypatch):
    async def _no_evidence(*a, **k):
        return []

    fake = _FakeOrch()
    monkeypatch.setattr(chat_api, "hybrid_retrieve", _no_evidence)
    monkeypatch.setattr(chat_api, "get_orchestrator", lambda: fake)

    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register", json={"username": "chat_user", "password": "pass123456"}
        )
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}

        r = c.post(
            "/api/v1/chat/stream", json={"query": "水利工程是什么", "thread_id": "t1"}, headers=h
        )
        assert r.status_code == 200
        text = r.text
        # 完整事件流：start / status / token / done
        assert "event: start" in text
        assert "event: status" in text
        assert "event: token" in text
        assert "event: done" in text
        # done 带真实 message_id（uuid 形式）
        assert '"message_id"' in text
        # 用户贯穿到 orchestrator
        assert fake.user_id


async def test_sse_disconnect_cancels_orchestrator(monkeypatch):
    """客户端断开（aclose → GeneratorExit）时后台 orchestrator 任务被取消（G9.3）。"""
    cancelled = threading.Event()

    class _BlockingOrch:
        """handle 永不完成，直到被取消——模拟长耗时生成。"""

        async def handle(self, agent_req):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def _no_evidence(*a, **k):
        return []

    async def _no_history(*a, **k):
        return []

    async def _no_save(*a, **k):
        return "mid"

    monkeypatch.setattr(chat_api, "_KEEPALIVE_S", 0.2)  # 心跳调短，加速进入 drain 循环
    monkeypatch.setattr(chat_api, "hybrid_retrieve", _no_evidence)
    monkeypatch.setattr(chat_api, "_load_history", _no_history)
    monkeypatch.setattr(chat_api, "_save_user_message", _no_save)
    monkeypatch.setattr(chat_api, "get_orchestrator", lambda: _BlockingOrch())
    fake_user = chat_api.CurrentUser(user_id="u1", username="t", role="user")

    gen = chat_api._chat_stream("水利工程是什么", "t1", fake_user)
    buf = ""
    # 推进事件流直到读到 keep-alive：此刻 orchestrator 后台任务已创建、生成器停在 drain 循环
    while ": keep-alive" not in buf:
        buf += await anext(gen)

    # 模拟客户端断开：aclose 在生成器当前挂起点（keep-alive yield）注入 GeneratorExit
    await gen.aclose()

    assert cancelled.wait(timeout=3), "客户端断开后 orchestrator 任务未被取消（孤儿）"


def test_chat_requires_auth():
    with TestClient(app) as c:
        r = c.post("/api/v1/chat/stream", json={"query": "x", "thread_id": "t"})
        assert r.status_code == 401  # 未认证


def test_chat_query_too_long_422():
    """G10.16：query 超长（>2000）在进入编排前被 422 拒绝——防止超大查询灌给 LLM。"""
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/register", json={"username": "len_chat", "password": "pass123456"})
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        r = c.post(
            "/api/v1/chat/stream",
            json={"query": "x" * 2001, "thread_id": "t"},
            headers=h,
        )
        assert r.status_code == 422
