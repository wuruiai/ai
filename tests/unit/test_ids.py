"""chunk_id / document_id 生成规则测试。"""

from backend.rag.ids import generate_chunk_id


def test_chunk_id_unique_per_page_and_index():
    ids = {generate_chunk_id("doc", p, i) for p in range(1, 4) for i in range(3)}
    assert len(ids) == 9  # 3 页 × 3 chunk 全部唯一


def test_chunk_id_same_input_same_output():
    assert generate_chunk_id("doc", 1, 0) == generate_chunk_id("doc", 1, 0)


def test_chunk_id_distinguishes_index():
    # 防止 DOCX 修复回退：同 page 不同 chunk_index 必须不同
    assert generate_chunk_id("doc", 1, 0) != generate_chunk_id("doc", 1, 1)


def test_chunk_id_distinguishes_page():
    assert generate_chunk_id("doc", 1, 0) != generate_chunk_id("doc", 2, 0)
