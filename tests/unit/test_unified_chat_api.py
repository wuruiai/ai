"""统一聊天接口测试（mock orchestrator）。"""

from fastapi.testclient import TestClient

from backend.api.v1 import unified_chat as uc
from backend.core.orchestrator import AgentResponse, AgentType
from backend.main import app


class _FakeOrch:
    async def handle(self, agent_req):
        return AgentResponse(
            success=True,
            agent_type=AgentType.KNOWLEDGE_QA,
            content="unified answer",
        )


def test_unified_chat_non_stream(monkeypatch):
    monkeypatch.setattr(uc, "get_orchestrator", lambda: _FakeOrch())
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/register", json={"username": "uni_user", "password": "pass123456"})
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}

        r = c.post(
            "/api/v1/unified-chat/",
            json={"message": "水利工程是什么", "session_id": "s1"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["content"] == "unified answer"


def test_unified_chat_requires_auth():
    with TestClient(app) as c:
        r = c.post("/api/v1/unified-chat/", json={"message": "x"})
        assert r.status_code == 401
