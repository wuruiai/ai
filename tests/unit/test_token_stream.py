"""LLM token 流回调测试。"""

import asyncio

from backend.core.token_stream import TokenStreamHandler


def test_handler_pushes_tokens_to_queue():
    q = asyncio.Queue()
    h = TokenStreamHandler(q)
    h.on_llm_new_token("水")
    h.on_llm_new_token("利")
    assert q.get_nowait() == ("token", "水")
    assert q.get_nowait() == ("token", "利")
    assert q.empty()
