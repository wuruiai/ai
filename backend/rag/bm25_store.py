"""FTS5/BM25 稀疏检索

全文检索封装。


chunks_fts 是 external content 模式（content='chunks'，content_rowid='rowid'）：
    - FTS 索引只存分词结果，不重复存 content
    - 不允许 INSERT INTO chunks_fts 直接写；由 chunks 表的 AI/AD/AU 触发器自动同步
    - 检索时通过 rowid JOIN 回 chunks 拿 chunk_id/document_id/page

中文检索策略（见准备文档附录 B）：
    - tokenizer 用 trigram：unicode61 不切分中文，导致中文查询 0 命中
    - trigram 要求查询 ≥3 连续字符；整句查询（"水利工程的主要功能是什么"）
      会被当作短语要求连续出现，导致 0 命中
    - 因此 search() 先把自然语言查询切成 3-6 字滑窗 n-gram，每块独立 MATCH，
      按 rowid 去重合并；避免把整句当短语
"""

from __future__ import annotations

import logging
import re

import aiosqlite

from backend.config import settings

# 非词字符（中文标点、英文空白、符号等）——用于把查询先切成语义片段
_SEP_RE = re.compile(r"[\s，。；、：？！!?（）()《》" r"''【】\-—\.,;:]+")


# FTS5 双引号：转义 term 内引号 + 用引号包裹成字符串字面量，
# 避免 AND/OR/NOT/NEAR 等被解析为操作符、或 * 被当通配符
def _fts_escape(term: str) -> str:
    """把 term 转成 FTS5 字符串字面量（防语法错误 / 防操作符误解析）。"""
    return '"' + term.replace('"', '""') + '"'


def _ngrams(text: str, lo: int = 3, hi: int = 6) -> list[str]:
    """把中文文本切成 lo..hi 连续字符滑窗，返回唯一片段（保序）。

    例："水利工程的主要功能" → ["水利工程", "利工程的", "工程的的", ...]（去重）
    """
    text = re.sub(r"\s+", "", text)
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    n = len(text)
    for w in range(min(hi, n), lo - 1, -1):  # 先长后短
        for i in range(n - w + 1):
            gram = text[i : i + w]
            if gram not in seen:
                seen.add(gram)
                out.append(gram)
    return out


def _split_terms(query: str) -> list[str]:
    """把自然语言查询转成若干可独立 MATCH 的词块。

    - 先按标点切成语义片段
    - 每片段切成 n-gram（3-6 字）
    - 整体控制数量，避免查询爆炸
    """
    fragments = [f for f in _SEP_RE.split(query) if f and len(f) >= 3]
    if not fragments:
        # 纯符号/过短：退回原查询
        return [query] if len(query) >= 3 else []
    grams: list[str] = []
    for frag in fragments:
        grams.extend(_ngrams(frag))
    # 去重 + 截断（最多 16 个查询词，防止 FTS MATCH 过长；原 12 会让长查询尾部召回丢失）
    seen: set[str] = set()
    terms: list[str] = []
    for g in grams:
        if g not in seen:
            seen.add(g)
            terms.append(g)
        if len(terms) >= 16:
            break
    return terms


class BM25Store:
    """BM25 存储（chunks FTS5 + chunks 表 JOIN）"""

    def __init__(self) -> None:
        self.db_path = settings.SQLITE_PATH

    async def search(
        self,
        query: str,
        top_k: int = 10,
        document_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """BM25 检索。返回 [{chunk_id, document_id, page, content, score}]"""
        terms = _split_terms(query)
        if not terms:
            return []

        async with aiosqlite.connect(self.db_path) as db:
            # 与 backend/db/connection.py 对齐：设 busy_timeout，避免写密集时读连接立刻 SQLITE_BUSY
            await db.execute("PRAGMA busy_timeout = 5000")
            # bm25() 不能在 GROUP BY / 聚合里用（FTS5 限制），只能用于 WHERE 过滤，
            # 因此每个 term 单独查询（带 LIMIT），Python 侧按 rowid 去重合并。
            # 数据隔离：JOIN documents 按 user_id 过滤（用户只能检索自己的文档）
            base_sql = """
                SELECT c.chunk_id, c.document_id, c.page, c.content,
                       bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks c ON c.rowid = chunks_fts.rowid
                JOIN documents d ON d.document_id = c.document_id
                WHERE chunks_fts MATCH ?
            """
            params: list = [None]  # [0] 为 term 占位
            if document_id:
                base_sql += " AND c.document_id = ?"
                params.append(document_id)
            if user_id:
                base_sql += " AND d.user_id = ?"
                params.append(user_id)
            base_sql += " ORDER BY rank LIMIT ?"
            params.append(top_k * 2)

            merged: dict[str, dict] = {}
            for raw_term in terms:
                # FTS5 转义：防 AND/OR/NOT/*/引号 触发语法错误
                term = _fts_escape(raw_term)
                params[0] = term
                try:
                    cur = await db.execute(base_sql, tuple(params))
                    rows = await cur.fetchall()
                except Exception as e:  # noqa: BLE001
                    # 单个 term 语法错误不致命：跳过该 term，继续其它
                    logging.getLogger(__name__).warning("FTS term %r failed: %s", raw_term, e)
                    continue
                for r in rows:
                    cid = r[0]
                    rank = float(r[4])
                    # 每个 chunk 保留最佳（最小 bm25）命中
                    if cid not in merged or rank < merged[cid]["_rank"]:
                        merged[cid] = {
                            "chunk_id": cid,
                            "document_id": r[1],
                            "page": r[2],
                            "content": r[3],
                            "score": -rank,  # bm25 越小越相关 → 转正分
                            "_rank": rank,
                        }
            results = sorted(
                (v for v in merged.values()),
                key=lambda x: x["_rank"],
            )[:top_k]
            for v in results:
                v.pop("_rank", None)
            return results


# 兼容 P0 历史 API：ingestion_worker 仍会调用 add_documents
# 但因 chunks_fts 是 external content，新增 chunks 由触发器自动同步，
# 这里 add_documents 仅作为 no-op 占位（让旧调用方不报错）。
async def add_documents(
    chunk_ids: list[str],
    contents: list[str],
    document_ids: list[str],
) -> None:
    """兼容旧 API：实际不写 FTS（FTS 由 chunks 触发器自动同步）。
    仅记录日志便于排查。"""
    import logging

    logging.getLogger(__name__).debug(
        "bm25.add_documents called with %d chunks (no-op, FTS synced via triggers)",
        len(chunk_ids),
    )


bm25_store = BM25Store()
