"""文档解析测试。"""

from docx import Document

from backend.rag.parser import _parse_docx_sync


def test_parse_docx_paragraphs_unique_page(tmp_path):
    """回归：多段 DOCX 每段必须有唯一 page，避免 chunk_id 碰撞。"""
    d = Document()
    d.add_paragraph("第一段：水利工程是治理水患的工程。")
    d.add_paragraph("第二段：水库调度统筹防洪与兴利。")
    d.add_paragraph("第三段：防汛预案需定期演练。")
    fp = tmp_path / "multi.docx"
    d.save(str(fp))

    pages = _parse_docx_sync(fp)
    assert len(pages) == 3
    assert [p["page"] for p in pages] == [1, 2, 3]
    assert all(p["content"].strip() for p in pages)


def test_parse_docx_skips_empty_paragraphs(tmp_path):
    d = Document()
    d.add_paragraph("")
    d.add_paragraph("有内容的一段")
    d.add_paragraph("   ")
    fp = tmp_path / "empty.docx"
    d.save(str(fp))

    pages = _parse_docx_sync(fp)
    assert len(pages) == 1  # 只保留非空段落
    assert pages[0]["page"] >= 1  # 页码可能因空段落跳过，但必须为正整数
