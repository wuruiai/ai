"""LLM token 流回调

把 LangChain LLM 的 streaming token 转推进 asyncio.Queue，
供 SSE 生成器并发读取实现"真流式"输出。
"""

from __future__ import annotations

import asyncio

from langchain_core.callbacks.base import BaseCallbackHandler


class TokenStreamHandler(BaseCallbackHandler):
    """把 on_llm_new_token 推入 asyncio.Queue。

    用法：把 [handler] 作为 callbacks 传给 ChatOpenAI(streaming=True)，
    生成器侧 while True: kind, payload = await queue.get() 消费。
    """

    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        # put_nowait 不阻塞：队列无界，避免在 LLM 回调里 await
        self.queue.put_nowait(("token", token))
