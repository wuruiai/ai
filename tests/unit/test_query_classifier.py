"""查询分类器测试（mock LLM）。"""

import asyncio

from backend.core import query_classifier as qc
from backend.core.query_classifier import QueryClassifier, QueryType


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, prompt):
        from langchain_core.messages import AIMessage

        return AIMessage(content=self._content)


def test_classify_general(monkeypatch):
    monkeypatch.setattr(
        qc.ModelFactory,
        "create_llm",
        lambda **k: _FakeLLM('{"type": "general", "confidence": 0.9}'),
    )
    qtype, conf = asyncio.run(QueryClassifier().classify("你好"))
    assert qtype == QueryType.GENERAL and conf == 0.9


def test_classify_specialized(monkeypatch):
    monkeypatch.setattr(
        qc.ModelFactory,
        "create_llm",
        lambda **k: _FakeLLM('{"type": "specialized", "confidence": 0.8}'),
    )
    qtype, conf = asyncio.run(QueryClassifier().classify("水库调度原则"))
    assert qtype == QueryType.SPECIALIZED and conf == 0.8


def test_classify_fallback_on_error(monkeypatch):
    class _BadLLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("cloud down")

    monkeypatch.setattr(qc.ModelFactory, "create_llm", lambda **k: _BadLLM())
    qtype, conf = asyncio.run(QueryClassifier().classify("水利"))
    assert qtype == QueryType.SPECIALIZED  # 降级默认专业问题
    assert conf == 0.5
