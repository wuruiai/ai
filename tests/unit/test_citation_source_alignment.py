"""G10.20 引用同源回归：citations 必须与喂给 LLM 的证据切片（evidence[:8]）同源同序。

此前 chat.py 独立 top-3 检索生成引用事件，与 LLM 实际依据的 rerank top-8 不是
同一批 chunk——答案里的 [N] 与引用面板对不上（来源错配/误导）。修复后 citations
由 generate_rag_node 与 evidence_text 同步产出，orchestrator 原样透传。
"""

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.knowledge_qa import nodes as kqa_nodes
from backend.core.model_factory import ModelFactory
from backend.core.orchestrator import AgentRequest, AgentType, Orchestrator


def _evidence(n: int) -> list[dict]:
    return [
        {
            "chunk_id": f"c{i}",
            "content": f"证据内容{i}",
            "document_id": "d1",
            "page": i,
            "score": i,
        }
        for i in range(n)
    ]


async def test_generate_rag_citations_match_prompt_evidence(monkeypatch):
    """citations == evidence[:8]，且与 system prompt 中的 [N] 编号一一对应。"""
    captured: dict = {}

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured["system"] = messages[0][1]
            return AIMessage(content="答案引用 [1] 和 [5]")

    monkeypatch.setattr(ModelFactory, "create_llm", lambda **kw: _FakeLLM())

    state = {
        "messages": [HumanMessage(content="水库调度")],
        "reranked_evidence": _evidence(10),
    }
    out = await kqa_nodes.generate_rag_node(state)

    assert len(out["citations"]) == 8
    assert [c["index"] for c in out["citations"]] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [c["source_id"] for c in out["citations"]] == [
        "c0",
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "c6",
        "c7",
    ]
    assert [c["page"] for c in out["citations"]] == [0, 1, 2, 3, 4, 5, 6, 7]

    # 系统提示含第 1 与第 8 条证据（[1]/[8]），与 citations 一一对应；9、10 未截入
    assert "[1] 证据内容0" in captured["system"]
    assert "[8] 证据内容7" in captured["system"]
    assert "证据内容8" not in captured["system"]


async def test_generate_rag_citations_fallback_evidence(monkeypatch):
    """无 rerank_evidence 时回退原始 evidence，citations 仍同源同序。"""

    class _FakeLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="回答")

    monkeypatch.setattr(ModelFactory, "create_llm", lambda **kw: _FakeLLM())

    state = {
        "messages": [HumanMessage(content="问题")],
        "evidence": _evidence(3),
    }
    out = await kqa_nodes.generate_rag_node(state)
    assert [c["source_id"] for c in out["citations"]] == ["c0", "c1", "c2"]
    assert out["citations"][0]["index"] == 1


async def test_orchestrator_propagates_citations(monkeypatch):
    """orchestrator 把图终态 citations 原样带进 AgentResponse（chat.py 依赖它出引用事件）。"""

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            return {
                "messages": [AIMessage(content="答案")],
                "citations": [{"index": 1, "source_id": "c1", "document_id": "d1"}],
            }

    orch = Orchestrator()
    monkeypatch.setattr(orch, "_get_agent_graph", lambda _t: _FakeGraph())
    resp = await orch.handle(
        AgentRequest(
            user_id="u1", session_id="s1", agent_type=AgentType.KNOWLEDGE_QA, user_message="q"
        )
    )
    assert resp.citations == [{"index": 1, "source_id": "c1", "document_id": "d1"}]
