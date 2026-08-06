"""S2 流式泄漏回归测试：辅助 LLM 不再把中间产物推到用户 SSE 流。

泄漏链路：chat.py 注入 `llm_callbacks=[TokenStreamHandler, UsageCollector]` →
knowledge_qa 分类器/HyDE/多查询节点拿完整链建 streaming LLM → 分类器 JSON 等
中间产物逐 token 进 SSE 队列 → 用户在最终答案前看到脏输出。

修复：`usage_only_callbacks()` 在辅助节点剔除 TokenStreamHandler（只留用量链），
最终答案生成节点仍用完整链。本文件验证该不变量在 helper 与节点两层都成立。
"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from backend.agents.knowledge_qa import nodes as kqa_nodes
from backend.core.model_factory import ModelFactory
from backend.core.query_classifier import QueryClassifier, QueryType
from backend.core.token_stream import TokenStreamHandler, usage_only_callbacks
from backend.core.usage import UsageCollector


def _chain():
    """返回 (stream_handler, usage_collector) 一对测试回调。"""
    return TokenStreamHandler(asyncio.Queue()), UsageCollector()


# ---------------------------------------------------------------------------
# helper 层：usage_only_callbacks
# ---------------------------------------------------------------------------


def test_usage_only_strips_stream_handler_keeps_usage():
    stream, usage = _chain()
    assert usage_only_callbacks([stream, usage]) == [usage]


def test_usage_only_passthrough_when_no_stream_handler():
    _, usage = _chain()
    # unified_chat 路径只传 [usage_collector]：原样保留（G10.7 记账不回退）
    assert usage_only_callbacks([usage]) == [usage]
    assert usage_only_callbacks([]) is None
    assert usage_only_callbacks(None) is None


def test_usage_only_returns_none_when_only_stream_handler():
    stream, _ = _chain()
    # 仅含 stream handler 的链（异常配置）→ None，走缓存单例路径
    assert usage_only_callbacks([stream]) is None


# ---------------------------------------------------------------------------
# 节点层：辅助节点剔除 stream handler，生成节点保留
# ---------------------------------------------------------------------------


async def test_classify_node_strips_stream_handler(monkeypatch):
    """分类节点收到的回调链不含 TokenStreamHandler（S2 泄漏点）。"""
    seen: dict = {}

    async def fake_classify(self, query, callbacks=None):
        seen["callbacks"] = callbacks
        return QueryType.SPECIALIZED, 0.9

    monkeypatch.setattr(QueryClassifier, "classify", fake_classify)
    stream, usage = _chain()
    state = {
        "messages": [HumanMessage(content="水库调度规则")],
        "llm_callbacks": [stream, usage],
    }
    await kqa_nodes.classify_query_node(state)
    assert seen["callbacks"] == [usage]


async def test_hyde_node_strips_stream_handler(monkeypatch):
    """HyDE 节点（VAGUE 路由）同样只挂用量链。"""
    seen: dict = {}

    async def fake_generate(self, query, callbacks=None):
        seen["callbacks"] = callbacks
        return "假设性文档"

    from backend.rag.hyde import HyDEGenerator

    monkeypatch.setattr(HyDEGenerator, "generate", fake_generate)
    stream, usage = _chain()
    state = {
        "original_query": "什么是防洪？",
        "llm_callbacks": [stream, usage],
    }
    await kqa_nodes.hyde_generate_node(state)
    assert seen["callbacks"] == [usage]


async def test_multi_query_node_strips_stream_handler(monkeypatch):
    """多查询改写节点（BROAD 路由）同样只挂用量链。"""
    seen: dict = {}

    async def fake_rewrite(self, query, num_queries=3, callbacks=None):
        seen["callbacks"] = callbacks
        return ["原始问题", "改写一"]

    from backend.rag.multi_query import MultiQueryRewriter

    monkeypatch.setattr(MultiQueryRewriter, "rewrite", fake_rewrite)
    stream, usage = _chain()
    state = {
        "original_query": "我国水资源分布",
        "llm_callbacks": [stream, usage],
    }
    await kqa_nodes.multi_query_rewrite_node(state)
    assert seen["callbacks"] == [usage]


async def test_generate_rag_node_keeps_full_chain(monkeypatch):
    """最终答案生成节点保留完整链：stream handler 仍能推 token（真流式不回归）。"""

    class _FakeLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="最终答案")

    seen: dict = {}

    def _factory(**kwargs):
        seen["callbacks"] = kwargs.get("callbacks")
        return _FakeLLM()

    monkeypatch.setattr(ModelFactory, "create_llm", _factory)
    stream, usage = _chain()
    state = {
        "messages": [HumanMessage(content="问题")],
        "reranked_evidence": [{"content": "证据一"}],
        "llm_callbacks": [stream, usage],
    }
    result = await kqa_nodes.generate_rag_node(state)
    assert seen["callbacks"] == [stream, usage]
    assert result["answer"] == "最终答案"


async def test_generate_direct_node_keeps_full_chain(monkeypatch):
    """直答（低置信度）节点同样保留完整链。"""

    class _FakeLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="直接回答")

    seen: dict = {}

    def _factory(**kwargs):
        seen["callbacks"] = kwargs.get("callbacks")
        return _FakeLLM()

    monkeypatch.setattr(ModelFactory, "create_llm", _factory)
    stream, usage = _chain()
    state = {
        "messages": [HumanMessage(content="闲聊")],
        "llm_callbacks": [stream, usage],
    }
    await kqa_nodes.generate_direct_node(state)
    assert seen["callbacks"] == [stream, usage]
