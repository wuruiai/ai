"""document_analysis 节点测试（S1 跨用户隔离）。"""

import asyncio

from backend.agents.document_analysis import nodes as da_nodes
from backend.rag.retriever import RetrievalResult


def test_load_document_scopes_retrieval_by_user_id(monkeypatch):
    """S1：load_document 检索必须带上 user_id，防止跨用户读取他人文档。"""
    captured: dict = {}

    async def _fake_retrieve(query, **kwargs):
        captured.update({"query": query, **kwargs})
        return [
            RetrievalResult(
                chunk_id="c1",
                content="片段内容",
                document_id=kwargs["document_id"],
                score=1.0,
                source="hybrid",
            )
        ]

    monkeypatch.setattr(da_nodes, "hybrid_retrieve", _fake_retrieve)

    state = {"document_id": "doc-123", "user_id": "u1", "query": "测试"}
    out = asyncio.run(da_nodes.load_document(state))
    assert out["document_loaded"] is True
    assert captured["document_id"] == "doc-123"
    assert captured["user_id"] == "u1"


def test_load_document_cross_user_returns_not_loaded(monkeypatch):
    """S1：检索带 user_id 后，他人文档检索为空 → 优雅失败而非返回内容。"""

    async def _empty_retrieve(**kwargs):
        return []

    monkeypatch.setattr(da_nodes, "hybrid_retrieve", _empty_retrieve)
    state = {"document_id": "victim-doc", "user_id": "u1", "query": "探测"}
    out = asyncio.run(da_nodes.load_document(state))
    assert out["document_loaded"] is False
    assert out["status"] == "failed"
