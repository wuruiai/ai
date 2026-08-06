"""编排器测试（mock 图）。"""

import asyncio

from backend.core.orchestrator import AgentRequest, AgentType, Orchestrator


class _FakeGraph:
    async def ainvoke(self, state, config=None):
        from langchain_core.messages import AIMessage

        return {
            "messages": [AIMessage(content="图答案")],
            "structured_output": None,
            "fallback_used": False,
        }


def test_run_single_agent(monkeypatch):
    orch = Orchestrator()
    monkeypatch.setattr(orch, "_get_agent_graph", lambda t: _FakeGraph())
    req = AgentRequest(
        user_id="u1", session_id="s1", agent_type=AgentType.KNOWLEDGE_QA, user_message="q"
    )
    resp = asyncio.run(orch._run_single_agent(req))
    assert resp.success and resp.content == "图答案"
    assert resp.agent_type == AgentType.KNOWLEDGE_QA


def test_handle_error_path(monkeypatch):
    orch = Orchestrator()

    def _boom(_t):
        raise ValueError("unknown agent")

    monkeypatch.setattr(orch, "_get_agent_graph", _boom)
    req = AgentRequest(
        user_id="u", session_id="s", agent_type=AgentType.WATER_EXPERT, user_message="q"
    )
    resp = asyncio.run(orch.handle(req))
    assert not resp.success
    assert resp.error_msg


def test_pipeline_aggregates(monkeypatch):
    orch = Orchestrator()
    monkeypatch.setattr(orch, "_get_agent_graph", lambda t: _FakeGraph())
    req = AgentRequest(
        user_id="u",
        session_id="s",
        agent_type=AgentType.KNOWLEDGE_QA,
        user_message="q",
        pipeline_mode=True,
        context={"pipeline_key": "document_qa"},
    )
    resp = asyncio.run(orch.handle(req))
    assert resp.success
    assert resp.content.count("图答案") >= 1  # 两段答案被拼接


def test_context_cannot_override_identity(monkeypatch):
    """S1：客户端 context 注入 user_id/session_id 不得覆盖已认证身份。"""
    captured: dict = {}
    fake = _FakeGraph()
    original_ainvoke = fake.ainvoke

    async def _capturing_ainvoke(state, config=None):
        captured.update(state)
        return await original_ainvoke(state, config)

    monkeypatch.setattr(fake, "ainvoke", _capturing_ainvoke)
    orch = Orchestrator()
    monkeypatch.setattr(orch, "_get_agent_graph", lambda t: fake)

    req = AgentRequest(
        user_id="u1",
        session_id="s1",
        agent_type=AgentType.DOCUMENT_ANALYSIS,
        user_message="分析",
        context={"user_id": "attacker", "session_id": "evil", "document_id": "doc-123"},
    )
    asyncio.run(orch._run_single_agent(req))
    # 身份字段始终取自已认证请求，document_id 等业务字段仍可透传
    assert captured["user_id"] == "u1"
    assert captured["student_id"] == "u1"
    assert captured["session_id"] == "s1"
    assert captured["document_id"] == "doc-123"
