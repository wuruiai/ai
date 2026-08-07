"""统一聊天接口测试（mock orchestrator）。"""

from fastapi.testclient import TestClient

from backend.api.v1 import unified_chat as uc
from backend.core.errors import ERROR_CODE_ORCHESTRATOR, GENERIC_ERROR_MESSAGE
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


def test_unified_chat_non_stream_persists_messages(monkeypatch):
    """S2：非流式端点同样落库 user + assistant 消息。

    修复前非流式从不 _save_user/_save_assistant_message——会话不进线程列表、
    多轮记忆丢该轮、反馈无目标。修复后与流式端点对齐：线程列表可见且含完整对话。
    """
    monkeypatch.setattr(uc, "get_orchestrator", lambda: _FakeOrch())
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register", json={"username": "persist_user", "password": "pass123456"}
        )
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}

        r = c.post(
            "/api/v1/unified-chat/",
            json={"message": "水库调度规则", "session_id": "s_persist"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["content"] == "unified answer"

        # 会话出现在线程列表（此前完全不落库，线程列表为空）
        r = c.get("/api/v1/threads/", headers=h)
        assert r.status_code == 200
        thread_ids = [t["thread_id"] for t in r.json()["threads"]]
        assert "s_persist" in thread_ids

        # 会话消息含用户提问 + 助手回复（多轮记忆/反馈的数据来源）
        r = c.get("/api/v1/threads/s_persist/messages", headers=h)
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        contents = {m["role"]: m["content"] for m in msgs}
        assert contents["user"] == "水库调度规则"
        assert contents["assistant"] == "unified answer"


def test_unified_chat_stream(monkeypatch):
    """流式端点：预算/用量链已接好，token 事件 + done 正常。"""

    class _StreamFakeOrch:
        async def handle(self, agent_req):
            cbs = agent_req.context.get("llm_callbacks") or []
            for token in "统一回答":
                for cb in cbs:
                    cb.on_llm_new_token(token)
            return AgentResponse(
                success=True,
                agent_type=AgentType.KNOWLEDGE_QA,
                content="统一回答",
            )

    fake = _StreamFakeOrch()
    monkeypatch.setattr(uc, "get_orchestrator", lambda: fake)
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register",
            json={"username": "uni_s_user", "password": "pass123456"},
        )
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}

        r = c.post(
            "/api/v1/unified-chat/stream",
            json={"message": "水利工程是什么", "session_id": "s2", "agent_type": "water_expert"},
            headers=h,
        )
        assert r.status_code == 200
        text = r.text
        assert "event: start" in text
        assert "event: token" in text
        assert "event: done" in text
        assert '"message_id"' in text


def test_unified_chat_requires_auth():
    with TestClient(app) as c:
        r = c.post("/api/v1/unified-chat/", json={"message": "x"})
        assert r.status_code == 401


def test_unified_chat_message_too_long_422():
    """G10.16：message 超长（>2000）在进入编排前被 422 拒绝，与 chat.py 对齐。"""
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/register", json={"username": "len_uni", "password": "pass123456"})
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        r = c.post("/api/v1/unified-chat/", json={"message": "x" * 2001}, headers=h)
        assert r.status_code == 422


class _CrashOrch:
    """handle 抛未预期异常（内部细节不得外泄）。"""

    async def handle(self, agent_req):
        raise RuntimeError("SECRET_INTERNAL_DETAILS: token=abc123 path=/app/data")


def test_unified_chat_crash_redacts_non_stream(monkeypatch):
    """G10.6：非流式 orchestrator 崩溃时，响应不含异常原文（脱敏为稳定码）。"""
    monkeypatch.setattr(uc, "get_orchestrator", lambda: _CrashOrch())
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register", json={"username": "redact_user", "password": "pass123456"}
        )
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        r = c.post(
            "/api/v1/unified-chat/",
            json={"message": "水利工程是什么", "session_id": "s_redact"},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert "SECRET_INTERNAL_DETAILS" not in body["error_msg"]
        assert body["error_msg"] == ERROR_CODE_ORCHESTRATOR
        assert "SECRET_INTERNAL_DETAILS" not in r.text
        assert body["content"] == GENERIC_ERROR_MESSAGE


def test_unified_chat_stream_crash_redacts(monkeypatch):
    """G10.6：流式 orchestrator 崩溃时，SSE 流不含异常原文（稳定 code + 通用文案）。"""
    monkeypatch.setattr(uc, "get_orchestrator", lambda: _CrashOrch())
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register", json={"username": "redact_s_user", "password": "pass123456"}
        )
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        r = c.post(
            "/api/v1/unified-chat/stream",
            json={"message": "水利工程是什么", "session_id": "s_redact_s"},
            headers=h,
        )
        assert r.status_code == 200
        text = r.text
        assert "event: error" in text
        assert "SECRET_INTERNAL_DETAILS" not in text
        assert ERROR_CODE_ORCHESTRATOR in text
        assert GENERIC_ERROR_MESSAGE in text


def test_unified_chat_stream_crash_records_budget(monkeypatch):
    """G10.7 M18：崩溃路径也记账——每日预算不能被持续触发错误绕过。

    记账在 finally（成功/失败/断开统一收口），崩溃返回后 record_call 必须已调用。
    """
    from backend.core.budget import budget_manager

    recorded: list = []
    monkeypatch.setattr(uc, "get_orchestrator", lambda: _CrashOrch())
    monkeypatch.setattr(
        budget_manager, "record_call", lambda user_id, model: recorded.append(user_id)
    )
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register", json={"username": "budget_user", "password": "pass123456"}
        )
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        r = c.post(
            "/api/v1/unified-chat/stream",
            json={"message": "水利工程是什么", "session_id": "s_budget"},
            headers=h,
        )
        assert r.status_code == 200
    assert recorded, "崩溃路径也应调用 budget_manager.record_call"
