"""LLM/Embedding/Rerank 统一入口

模型工厂，统一管理云模型调用。

Reference: §4.2
"""

from langchain_openai import ChatOpenAI

from backend.config import settings


class ModelFactory:
    """模型工厂"""

    @staticmethod
    def create_llm(
        model: str | None = None,
        temperature: float = 0.7,
        callbacks: list | None = None,
    ):
        """创建 LLM 实例（含重试与超时，见方案文档 §4.4）。

        callbacks: 传 TokenStreamHandler 列表时开启 streaming，
        便于 SSE 端逐 token 推送（真流式）。
        """
        return ChatOpenAI(
            model=model or settings.LLM_MODEL,
            api_key=settings.DASHSCOPE_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
            temperature=temperature,
            timeout=settings.LLM_TIMEOUT_S,
            # 偶发限流/超时自动重试（指数退避最多 max_retries 次）
            max_retries=max(2, settings.MAX_RETRIES),
            request_timeout=settings.LLM_TIMEOUT_S,
            # streaming=True 时 ainvoke 仍返回完整内容，但会逐 token 触发回调
            streaming=True,
            callbacks=callbacks or [],
        )

    # Embedding 统一走 backend.rag.embedding.get_embeddings()（openai SDK 直连，
    # 绕开 langchain OpenAIEmbeddings 的 list 序列化 bug）。
    # Rerank 统一走 backend.rag.reranker.rerank()（DashScope 原生协议）。
    # 两者都不在此重复实现，避免双入口。
