"""索引重建脚本

从 chunks 表重灌 Chroma 向量索引（新建—评测—切换）。

适用场景：
    - Embedding 模型升级（维度变化）后重建
    - Chroma 数据损坏后从 SQLite 恢复
    - 索引与实际内容不一致时校准

Usage:
    python -m scripts.rebuild_index
    python -m scripts.rebuild_index --chunk-size 200    # 每批 embedding 数量

流程：
    1. 读 chunks 表全量 chunk_id/content/document_id/page
    2. 删除旧 Chroma collection（重建同名）
    3. 分批 embed（避免一次请求过大）+ 写入 Chroma
    4. 输出统计

注意：FTS5 索引由 SQLite 触发器自动同步，本脚本不触碰 chunks 表本身。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings
from backend.db.connection import close_db, close_pool, get_connection


async def _load_all_chunks() -> list[dict]:
    db = await get_connection()
    try:
        async with db.execute(
            "SELECT c.chunk_id, c.content, c.document_id, c.page, c.chunk_index, "
            "       COALESCE(d.user_id, 'local_user') AS user_id "
            "FROM chunks c LEFT JOIN documents d ON d.document_id = c.document_id "
            "ORDER BY c.document_id, c.chunk_index"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "chunk_id": r[0],
                "content": r[1],
                "document_id": r[2],
                "page": r[3],
                "chunk_index": r[4],
                "user_id": r[5],
            }
            for r in rows
        ]
    finally:
        await close_db(db)
        # G10.24：一次性脚本收尾关闭连接池，避免 aiosqlite 后台线程拖住解释器
        await close_pool()


async def rebuild(chunk_size: int = 128) -> int:
    import chromadb

    from backend.rag.embedding import get_embeddings

    chunks = await _load_all_chunks()
    if not chunks:
        print("OK 无 chunks 需要重建")
        return 0

    print(f"共 {len(chunks)} 个 chunk 待重建...")

    # 重建 Chroma collection（drop 旧集合，重新 get_or_create）
    client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
    try:
        client.delete_collection(settings.CHROMA_COLLECTION)
    except Exception:  # noqa: S110 -- 集合不存在/未初始化时直接重建
        pass
    collection = client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    total = 0
    for start in range(0, len(chunks), chunk_size):
        batch = chunks[start : start + chunk_size]
        texts = [c["content"] for c in batch]

        # 过滤空文本（embedding 会失败）
        kept, kept_ids, kept_texts = [], [], []
        for c, t in zip(batch, texts, strict=False):
            if t and t.strip():
                kept.append(c)
                kept_ids.append(c["chunk_id"])
                kept_texts.append(t)
        if not kept:
            continue

        embeddings = await get_embeddings(kept_texts)
        collection.add(
            ids=kept_ids,
            documents=kept_texts,
            embeddings=embeddings,
            metadatas=[
                {
                    "document_id": c["document_id"],
                    "page": c.get("page"),
                    "user_id": c.get("user_id", "local_user"),
                }
                for c in kept
            ],
        )
        total += len(kept)
        print(f"  ✓ {start + len(kept)}/{len(chunks)}")

    print(f"OK 重建完成：{total} 个 chunk 已写入 Chroma")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="重建向量索引")
    parser.add_argument("--chunk-size", type=int, default=128, help="每批 embedding 数量")
    args = parser.parse_args()
    return asyncio.run(rebuild(chunk_size=args.chunk_size))


if __name__ == "__main__":
    sys.exit(main())
