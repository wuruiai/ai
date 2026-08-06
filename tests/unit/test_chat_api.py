"""聊天流式 API 测试（mock orchestrator + 模拟 token 回调）。"""

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


def test_chat_requires_auth():
    with TestClient(app) as c:
        r = c.post("/api/v1/chat/stream", json={"query": "x", "thread_id": "t"})
        assert r.status_code == 401  # 未认证
