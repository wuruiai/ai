"""切分器契约测试。"""

from backend.rag.chunker import chunk_pages


def test_chunk_pages_single_short_text():
    chunks = chunk_pages([{"page": 1, "content": "短文本"}], chunk_size=512)
    assert len(chunks) == 1
    assert chunks[0]["page"] == 1
    assert chunks[0]["chunk_index"] == 0


def test_chunk_pages_long_text_splits():
    chunks = chunk_pages([{"page": 1, "content": "水" * 1200}], chunk_size=512)
    assert len(chunks) >= 2
    # 每页 chunk_index 从 0 连续递增
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_chunk_pages_per_page_index_resets():
    pages = [
        {"page": 1, "content": "甲" * 600},
        {"page": 2, "content": "乙" * 600},
    ]
    chunks = chunk_pages(pages, chunk_size=512)
    p1 = [c for c in chunks if c["page"] == 1]
    p2 = [c for c in chunks if c["page"] == 2]
    assert [c["chunk_index"] for c in p1] == list(range(len(p1)))
    assert [c["chunk_index"] for c in p2] == list(range(len(p2)))


def test_chunk_pages_missing_page_defaults_1():
    chunks = chunk_pages([{"content": "无 page 字段"}], chunk_size=512)
    assert chunks[0]["page"] == 1
