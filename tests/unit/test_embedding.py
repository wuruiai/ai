"""embedding 封装测试（不调云端）。"""

import asyncio

import pytest

from backend.rag.embedding import get_embeddings


def test_get_embeddings_empty_input_returns_empty():
    assert asyncio.run(get_embeddings([])) == []


def test_get_embeddings_all_empty_returns_empty():
    assert asyncio.run(get_embeddings(["", "  "])) == []


def test_get_embeddings_mixed_empty_raises():
    """混入空串会导致返回长度与入参错位，应显式报错而非静默错位。"""
    with pytest.raises(ValueError):
        asyncio.run(get_embeddings(["正常文本", ""]))
