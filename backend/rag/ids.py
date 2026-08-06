"""稳定 ID 生成（sha256 规则）

文档和 chunk 的 ID 生成。

"""

import hashlib


def _stable_hash(content: str) -> str:
    """确定性哈希 → 32 位十六进制 ID。

    用 sha256（而非 md5）保证碰撞安全性；截断到 32 位，
    与旧版 md5 ID 长度一致，兼容已有存储。
    """
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def generate_document_id(file_path: str, file_size: int) -> str:
    """生成文档 ID"""
    return _stable_hash(f"{file_path}:{file_size}")


def generate_chunk_id(document_id: str, page: int, chunk_index: int) -> str:
    """生成 chunk ID"""
    return _stable_hash(f"{document_id}:{page}:{chunk_index}")
