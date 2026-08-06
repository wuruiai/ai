"""辅助 LLM 用量记账（G10.7 M1）。

query_classifier / hyde / multi_query 是检索链路上的辅助 LLM 调用，
此前创建 LLM 时不带 callbacks → 这些 token 消耗不在 llm_usage 中（成本黑洞）。
带 callbacks 调用时必须透传给 ModelFactory.create_llm（且不污染模块单例）。
"""

import asyncio

from backend.core.model_factory import ModelFactory
from backend.core.query_classifier import QueryClassifier, QueryType
from backend.rag.hyde import HyDEGenerator
from backend.rag.multi_query import MultiQueryRewriter


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, prompt):
        from langchain_core.messages import AIMessage

        return AIMessage(content=self._content)


def _patch_factory(monkeypatch, content: str, seen: dict):
    """monkeypatch ModelFactory.create_llm：记录 kwargs（重点验证 callbacks 透传）。"""

    def _factory(**kwargs):
        seen.update(kwargs)
        return _FakeLLM(content)

    monkeypatch.setattr(ModelFactory, "create_llm", _factory)


def test_query_classifier_passes_callbacks(monkeypatch):
    seen: dict = {}
    _patch_factory(monkeypatch, '{"type": "specialized", "confidence": 0.8}', seen)
    cb = object()
    qtype, _ = asyncio.run(QueryClassifier().classify("水库调度", callbacks=[cb]))
    assert qtype == QueryType.SPECIALIZED
    assert seen.get("callbacks") == [cb]


def test_hyde_passes_callbacks(monkeypatch):
    seen: dict = {}
    _patch_factory(monkeypatch, "假设性答案文本内容", seen)
    cb = object()
    doc = asyncio.run(HyDEGenerator().generate("模糊查询", callbacks=[cb]))
    assert "假设性答案" in doc
    assert seen.get("callbacks") == [cb]


def test_multi_query_passes_callbacks(monkeypatch):
    seen: dict = {}
    _patch_factory(monkeypatch, "角度一\n角度二\n角度三", seen)
    cb = object()
    queries = asyncio.run(MultiQueryRewriter().rewrite("原始问题", num_queries=3, callbacks=[cb]))
    assert queries[0] == "原始问题"  # 原始查询置于首位
    assert seen.get("callbacks") == [cb]


def test_no_callbacks_uses_singleton_llm(monkeypatch):
    """不带 callbacks（独立/脚本调用）仍走模块单例 LLM，不新建实例。"""
    calls: list[dict] = []

    def _factory(**kwargs):
        calls.append(kwargs)
        return _FakeLLM('{"type": "specialized", "confidence": 0.8}')

    monkeypatch.setattr(ModelFactory, "create_llm", _factory)
    qtype, _ = asyncio.run(QueryClassifier().classify("水利"))
    assert qtype == QueryType.SPECIALIZED
    # 仅 __init__ 创建过一次单例 LLM；classify 不带回调 → 复用，不再新建
    assert len(calls) == 1
