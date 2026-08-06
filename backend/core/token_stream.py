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


def usage_only_callbacks(callbacks: list | None) -> list | None:
    """从回调链剔除 TokenStreamHandler，仅保留用量链——辅助 LLM（分类器/HyDE/多查询）专用。

    背景（S2 流式泄漏）：chat.py 把 [TokenStreamHandler, UsageCollector] 一并注入
    `llm_callbacks`。若辅助 LLM 也拿到完整链，分类器 JSON / 多查询改写等中间产物会
    逐 token 推入 SSE 队列，用户在最终答案前看到一段脏输出。辅助 LLM 只应记账、
    不应流式，故剔除 stream handler；最终答案生成节点仍用完整回调链（流式 + 用量）。
    返回 None 时（原链为空或仅含 stream handler），调用方走无回调的缓存单例路径。
    """
    if not callbacks:
        return None
    filtered = [cb for cb in callbacks if not isinstance(cb, TokenStreamHandler)]
    return filtered or None
