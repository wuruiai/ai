"""Chroma 读写封装

向量数据库操作。

Reference: §6.6
"""

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.config import settings


def silence_chroma_telemetry() -> None:
    """压制 chromadb 0.6.3 的遥测噪声（幂等）。

    根因：chromadb 用 posthog.capture(user_id, event, props) 传 3 个位置参数，
    但 posthog 的 capture(event, **kwargs) 只接受 1 个位置参数，参数绑定阶段就抛
    TypeError，且发生在 disabled 标志判断之前（所以 env / Settings 都压不住）。
    → 直接把 capture 换成语义一致的 no-op。
    """
    try:
        import posthog

        if not getattr(posthog, "_waterrag_capture_patched", False):
            posthog.capture = lambda *args, **kwargs: None
            posthog._waterrag_capture_patched = True
    except Exception:  # noqa: BLE001, S110 -- 打不上也不影响功能（纯增益，幂等）
        pass


class VectorStore:
    """向量存储"""

    def __init__(self):
        silence_chroma_telemetry()
        self._client = chromadb.PersistentClient(
            path=settings.CHROMA_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def add_documents(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict] | None = None,
    ):
        """添加文档"""
        self._collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ) -> dict:
        """查询（n_results 自动 clamp 到当前索引条目数，避免 chroma 报错）。"""
        count = self.count()
        if count == 0:
            # 空集合：直接返回空结果，避免 chroma 报 "n_results > elements"
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}
        effective = max(1, min(n_results, count))
        return self._collection.query(
            query_embeddings=[query_embedding],
            n_results=effective,
            where=where,
        )

    def delete(self, ids: list[str]):
        """按 chunk_id 列表删除"""
        if ids:
            self._collection.delete(ids=ids)

    def delete_by_document(self, document_id: str, retries: int = 3) -> int:
        """按 document_id 删除该文档的全部向量（metadata where 过滤）。

        返回删除的条数；找不到时返回 0。删除文档必须调此方法，
        否则残留向量会被检索到（幽灵结果）。

        失败重试 retries 次；最终失败抛异常（由调用方记录），
        避免静默吞错导致幽灵向量无法清理。
        """
        import logging

        logger = logging.getLogger(__name__)

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                # 先取该文档的 chunk_id（Chroma delete 支持 where 过滤）
                res = self._collection.get(where={"document_id": document_id})
                ids = res.get("ids", [])
                if ids:
                    self._collection.delete(ids=ids)
                return len(ids)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt < retries:
                    import time

                    time.sleep(0.5 * attempt)  # 退避重试
                else:
                    logger.error(
                        "delete_by_document %s failed after %d attempts: %s",
                        document_id[:12],
                        retries,
                        e,
                    )
        raise last_exc  # type: ignore[misc]

    def count(self) -> int:
        """获取文档数量"""
        return self._collection.count()


vector_store = VectorStore()
