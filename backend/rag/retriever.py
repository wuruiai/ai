"""统一检索入口

混合检索器。

Reference: §6.4
"""

from dataclasses import dataclass

from backend.config import settings
from backend.core.logger import get_logger
from backend.rag.bm25_store import bm25_store
from backend.rag.embedding import get_embedding
from backend.rag.vector_store import vector_store

logger = get_logger(__name__)


@dataclass
class RetrievalResult:
    """检索结果"""

    chunk_id: str
    content: str
    document_id: str
    score: float  # 融合后的分数（越高越相关）
    source: str  # "dense" | "sparse" | "hybrid"
    page: int | None = None  # 页码（仅 sparse 有；dense 无）
    dense_score: float = 0.0  # 原始 dense 归一化分数（0-1）
    sparse_score: float = 0.0  # 原始 sparse 归一化分数（0-1）


def _normalize(scores: list[float]) -> list[float]:
    """min-max 归一化到 [0,1]；长度 <2 或全相等返回全 0.5。"""
    if len(scores) < 2:
        return [0.5] * len(scores)
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-12:
        return [0.5] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


async def retrieve(
    query: str,
    top_k: int | None = None,
    document_id: str | None = None,
    user_id: str | None = None,
) -> list[RetrievalResult]:
    """混合检索：dense + sparse 加权融合（§6.4）。

    融合策略：
        1. 向量检索（cosine 距离，越小越相关）→ 归一化
        2. BM25 检索（bm25 负数，越小越相关）→ 归一化
        3. 同一 chunk 的 dense / sparse 分数按 DENSE_WEIGHT/SPARSE_WEIGHT 加权，
           只命中一路的按该路权重计
        4. 降序排序取 top_k

    数据隔离：传 user_id 时，dense/sparse 都只检索该用户拥有的文档。
    """
    top_k = top_k or settings.RERANK_TOP_K
    dense_w = settings.DENSE_WEIGHT
    sparse_w = settings.SPARSE_WEIGHT

    # 检索范围：用户文档（可叠加单文档过滤）
    where = None
    if user_id or document_id:
        conds: list[dict] = []
        if user_id:
            conds.append({"user_id": user_id})
        if document_id:
            conds.append({"document_id": document_id})
        where = conds[0] if len(conds) == 1 else {"$and": conds}

    # ---------- 向量检索 ----------
    dense_results = []
    try:
        query_embedding = await get_embedding(query)
        vs_query = await vector_store.query(
            query_embedding=query_embedding,
            n_results=settings.DENSE_TOP_K,
            where=where,
        )
        if vs_query and vs_query.get("ids"):
            for i, chunk_id in enumerate(vs_query["ids"][0]):
                dist = vs_query["distances"][0][i] if vs_query.get("distances") else 0
                dense_results.append(
                    {
                        "chunk_id": chunk_id,
                        "content": vs_query["documents"][0][i],
                        "document_id": vs_query["metadatas"][0][i].get("document_id", ""),
                        "raw": float(dist),
                    }
                )
    except Exception as e:  # noqa: BLE001
        # embedding/向量库故障时降级为仅稀疏检索（与下方 BM25 段的降级语义对称，
        # 避免 retrieve() 整体抛异常打挂整个对话）
        logger.warning("dense retrieval failed, degrading to sparse-only: %s", e)
        dense_results = []

    # ---------- BM25 检索 ----------
    sparse_results = []
    try:
        sp = await bm25_store.search(
            query=query,
            top_k=settings.BM25_TOP_K,
            document_id=document_id,
            user_id=user_id,
        )
        for r in sp:
            sparse_results.append(
                {
                    "chunk_id": r["chunk_id"],
                    "content": r["content"],
                    "document_id": r["document_id"],
                    # bm25_store.search 已返回 -rank（正数，越大越相关）；直接使用，
                    # 不能再取反（此前双重取反导致最相关映射到 0，排序错乱）
                    "raw": float(r["score"]),
                }
            )
    except Exception:  # noqa: BLE001 -- FTS 空表/无命中时不阻断 dense（有意降级）
        sparse_results = []

    # ---------- 归一化 ----------
    dense_vals = [r["raw"] for r in dense_results]  # cosine 距离，越小越好
    sparse_vals = [r["raw"] for r in sparse_results]  # 已转正，越大越好

    dense_norm = _normalize([-v for v in dense_vals])  # 距离 → 相似度（越大越好）
    sparse_norm = _normalize(sparse_vals)

    for i, r in enumerate(dense_results):
        r["dense_score"] = dense_norm[i] if dense_norm else 0.0
    for i, r in enumerate(sparse_results):
        r["sparse_score"] = sparse_norm[i] if sparse_norm else 0.0

    # ---------- 融合 ----------
    merged: dict[str, RetrievalResult] = {}
    for r in dense_results:
        merged[r["chunk_id"]] = RetrievalResult(
            chunk_id=r["chunk_id"],
            content=r["content"],
            document_id=r["document_id"],
            score=dense_w * r["dense_score"],
            source="dense",
            dense_score=r["dense_score"],
            sparse_score=0.0,
        )
    for r in sparse_results:
        if r["chunk_id"] in merged:
            hit = merged[r["chunk_id"]]
            hit.sparse_score = r["sparse_score"]
            hit.score = dense_w * hit.dense_score + sparse_w * r["sparse_score"]
            hit.source = "hybrid"
            if hit.page is None and r.get("page") is not None:
                hit.page = r.get("page")
        else:
            merged[r["chunk_id"]] = RetrievalResult(
                chunk_id=r["chunk_id"],
                content=r["content"],
                document_id=r["document_id"],
                score=sparse_w * r["sparse_score"],
                source="sparse",
                page=r.get("page"),
                dense_score=0.0,
                sparse_score=r["sparse_score"],
            )

    results = sorted(merged.values(), key=lambda x: x.score, reverse=True)
    return results[:top_k]
