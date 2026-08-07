"""knowledge_qa Agent 图测试（mock 全部 LLM/检索/重排触点，G10.25 测试缺口补齐）。

此前该图无任何图级测试。覆盖：
    - 条件路由函数（查询类型 5 类 + 未知兜底；置信度高/低）
    - 端到端图执行（GENERAL→直答；SPECIALIZED→检索→重排→RAG 生成带 citations）
    - 节点级：rerank 失败降级、HyDE 生成、多查询重写
"""

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.knowledge_qa import nodes
from backend.agents.knowledge_qa.graph import (
    _route_by_confidence,
    _route_by_query_type,
    build_knowledge_qa_graph,
)
from backend.agents.knowledge_qa.nodes import hyde_generate_node, multi_query_rewrite_node
from backend.core.confidence_router import ConfidenceLevel
from backend.core.query_classifier import QueryType
from backend.rag.retriever import RetrievalResult

# ---------------------------------------------------------------------------
# 条件路由函数（纯函数）
# ---------------------------------------------------------------------------


def test_route_by_query_type_maps_all_valid_types():
    assert _route_by_query_type({"query_type": "PRECISE"}) == "PRECISE"
    assert _route_by_query_type({"query_type": "SPECIALIZED"}) == "SPECIALIZED"
    assert _route_by_query_type({"query_type": "VAGUE"}) == "VAGUE"
    assert _route_by_query_type({"query_type": "BROAD"}) == "BROAD"
    assert _route_by_query_type({"query_type": "GENERAL"}) == "GENERAL"


def test_route_by_query_type_unknown_falls_back_to_general():
    """容错：未来分类器产出未知类型时兜底 GENERAL（直答），绝不 KeyError 崩溃。"""
    assert _route_by_query_type({"query_type": "WEIRD"}) == "GENERAL"
    # 大小写不敏感
    assert _route_by_query_type({"query_type": "vague"}) == "VAGUE"


def test_route_by_query_type_missing_defaults_to_precise():
    """状态里没写 query_type 时按 PRECISE 走 RAG（保守默认）。"""
    assert _route_by_query_type({}) == "PRECISE"


def test_route_by_confidence():
    assert _route_by_confidence({"is_high_confidence": True}) == "high"
    assert _route_by_confidence({"is_high_confidence": False}) == "low"
    assert _route_by_confidence({}) == "low"


# ---------------------------------------------------------------------------
# 端到端图执行
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return AIMessage(content=self._content)


class _FakeClassifier:
    def __init__(self, qtype: QueryType):
        self._qtype = qtype
        self.calls: list[str] = []

    async def classify(self, query, callbacks=None):
        self.calls.append("classify")
        return self._qtype, 0.9


class _FakeConfidenceRouter:
    def __init__(self, level: ConfidenceLevel, score: float):
        self._level, self._score = level, score
        self.evidence_seen: list | None = None

    def evaluate(self, evidence):
        self.evidence_seen = evidence
        return self._level, self._score


class _FakeRetriever:
    def __init__(self, results: list):
        self._results = results
        self.calls: list[str] = []

    async def __call__(self, query, user_id=None):
        self.calls.append(query)
        return self._results


class _FakeReranker:
    def __init__(self, outcome):
        self._outcome = outcome  # list[dict] 或 Exception
        self.calls: list[str] = []

    async def __call__(self, query, documents):
        self.calls.append(query)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _evidence(chunk_id: str, doc_id: str, score: float, content: str, page: int | None = None):
    return RetrievalResult(
        chunk_id=chunk_id,
        content=content,
        document_id=doc_id,
        score=score,
        source="hybrid",
        page=page,
    )


async def test_graph_general_query_answers_directly(monkeypatch):
    """GENERAL（通用问题）→ generate_direct：不检索、不 RAG，直接 LLM 回答。"""
    classifier = _FakeClassifier(QueryType.GENERAL)
    llm = _FakeLLM("你好！我是水利助手。")
    monkeypatch.setattr(nodes, "get_query_classifier", lambda: classifier)
    monkeypatch.setattr(nodes.ModelFactory, "create_llm", lambda **k: llm)

    graph = build_knowledge_qa_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content="你好")]})

    assert classifier.calls == ["classify"]
    assert result["answer"] == "你好！我是水利助手。"
    assert result["llm_call_count"] == 1
    assert result["messages"][-1].content == "你好！我是水利助手。"


async def test_graph_specialized_high_confidence_rag_with_citations(monkeypatch):
    """SPECIALIZED → PRECISE → retrieve → rerank → generate_rag（HIGH 置信）。

    G10.20 引用同源：citations 与喂给 LLM 的证据切片同源同序，[N] 一一对应。
    """
    classifier = _FakeClassifier(QueryType.SPECIALIZED)
    router = _FakeConfidenceRouter(ConfidenceLevel.HIGH, 0.85)
    retriever = _FakeRetriever(
        [
            _evidence("c1", "d1", 0.9, "水库调度以汛期防洪为先。", 2),
            _evidence("c2", "d2", 0.7, "大坝安全鉴定每五年一次。", 5),
        ]
    )
    # 重排打乱顺序：d2 的 chunk 提到最前
    reranker = _FakeReranker([{"index": 1, "score": 0.95}, {"index": 0, "score": 0.6}])
    llm = _FakeLLM("依据证据：大坝安全鉴定每五年一次 [1]，水库调度以汛期防洪为先 [2]。")

    monkeypatch.setattr(nodes, "get_query_classifier", lambda: classifier)
    monkeypatch.setattr(nodes, "get_confidence_router", lambda: router)
    monkeypatch.setattr(nodes, "retrieve", retriever)
    monkeypatch.setattr(nodes, "rerank", reranker)
    monkeypatch.setattr(nodes.ModelFactory, "create_llm", lambda **k: llm)

    graph = build_knowledge_qa_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content="水库调度与大坝安全")]})

    # 分支：检索与重排都被走到，且查询类型映射为 PRECISE 语义
    assert retriever.calls == ["水库调度与大坝安全"]
    assert reranker.calls == ["水库调度与大坝安全"]
    assert result["is_high_confidence"] is True
    assert result["answer"].startswith("依据证据：")

    # 重排后 citations 与新序同源同序：第一条是原 index=1 的 c2（d2/第 5 页）
    assert result["citations"][0] == {
        "index": 1,
        "source_id": "c2",
        "document_id": "d2",
        "page": 5,
        "content": "大坝安全鉴定每五年一次。",
    }
    assert result["citations"][1]["source_id"] == "c1"
    assert result["citations"][1]["document_id"] == "d1"
    assert result["citations"][1]["page"] == 2


async def test_graph_low_confidence_goes_direct(monkeypatch):
    """检索到证据但置信度 LOW → 仍走检索/重排，但最终 generate_direct（无 citations）。"""
    classifier = _FakeClassifier(QueryType.SPECIALIZED)
    router = _FakeConfidenceRouter(ConfidenceLevel.LOW, 0.1)
    retriever = _FakeRetriever([_evidence("c1", "d1", 0.1, "弱相关片段。")])
    reranker = _FakeReranker([{"index": 0, "score": 0.0}])
    llm = _FakeLLM("证据不足，我直接说明。")

    monkeypatch.setattr(nodes, "get_query_classifier", lambda: classifier)
    monkeypatch.setattr(nodes, "get_confidence_router", lambda: router)
    monkeypatch.setattr(nodes, "retrieve", retriever)
    monkeypatch.setattr(nodes, "rerank", reranker)
    monkeypatch.setattr(nodes.ModelFactory, "create_llm", lambda **k: llm)

    graph = build_knowledge_qa_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content="某专业问题")]})

    assert result["is_high_confidence"] is False
    assert result["answer"] == "证据不足，我直接说明。"
    # 低置信度直答不是降级（generate_direct_node 不再置 fallback_used）
    assert result.get("fallback_used") is False


# ---------------------------------------------------------------------------
# 节点级：rerank 降级 / HyDE / 多查询
# ---------------------------------------------------------------------------


async def test_rerank_node_failure_degrades_to_raw_evidence(monkeypatch):
    """rerank 失败（如 403 未开通）→ 降级用原始检索结果，不阻断后续生成。"""
    retriever = _FakeRetriever(
        [
            _evidence("c1", "d1", 0.9, "原始检索结果 A。"),
            _evidence("c2", "d2", 0.8, "原始检索结果 B。"),
        ]
    )
    reranker = _FakeReranker(RuntimeError("dashscope rerank forbidden"))
    llm = _FakeLLM("降级后的 RAG 回答。")
    router = _FakeConfidenceRouter(ConfidenceLevel.HIGH, 0.85)

    monkeypatch.setattr(
        nodes, "get_query_classifier", lambda: _FakeClassifier(QueryType.SPECIALIZED)
    )
    monkeypatch.setattr(nodes, "get_confidence_router", lambda: router)
    monkeypatch.setattr(nodes, "retrieve", retriever)
    monkeypatch.setattr(nodes, "rerank", reranker)
    monkeypatch.setattr(nodes.ModelFactory, "create_llm", lambda **k: llm)

    graph = build_knowledge_qa_graph()
    result = await graph.ainvoke({"messages": [HumanMessage(content="专业问题")]})

    assert result["fallback_used"] is True  # rerank 降级标记
    assert result["reranked_evidence"][0]["content"] == "原始检索结果 A。"
    assert result["reranked_evidence"][0]["rerank_score"] == 0.0
    assert result["answer"] == "降级后的 RAG 回答。"


async def test_hyde_generate_node(monkeypatch):
    """VAGUE 预处理节点：生成假设性文档并作为查询喂给检索。"""
    calls: list[str] = []

    class _FakeHyde:
        async def generate(self, query, callbacks=None):
            calls.append("generate")
            return f"假设性文档：{query}"

    monkeypatch.setattr(nodes, "get_hyde_generator", lambda: _FakeHyde())

    out = await hyde_generate_node({"original_query": "什么是兴利调度"})
    assert calls == ["generate"]
    assert out["hypothetical_doc"] == "假设性文档：什么是兴利调度"
    assert out["queries"] == ["假设性文档：什么是兴利调度"]
    assert out["llm_call_count"] == 1


async def test_multi_query_rewrite_node(monkeypatch):
    """BROAD 预处理节点：多角度改写后喂给检索。"""
    calls: list[str] = []

    class _FakeRewriter:
        async def rewrite(self, query, callbacks=None):
            calls.append("rewrite")
            return [f"{query} 角度1", f"{query} 角度2"]

    monkeypatch.setattr(nodes, "get_multi_query_rewriter", lambda: _FakeRewriter())

    out = await multi_query_rewrite_node({"original_query": "水库调度"})
    assert calls == ["rewrite"]
    assert out["queries"] == ["水库调度 角度1", "水库调度 角度2"]
    assert out["llm_call_count"] == 1
