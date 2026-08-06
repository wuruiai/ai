"""PDF/DOCX 解析

文档解析器（run_in_executor）。

"""

import asyncio
from pathlib import Path


async def parse_pdf(file_path: Path) -> list[dict]:
    """解析 PDF 文件"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _parse_pdf_sync, file_path)


def _parse_pdf_sync(file_path: Path) -> list[dict]:
    """同步解析 PDF"""
    import fitz  # PyMuPDF

    doc = fitz.open(str(file_path))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        pages.append({"page": i + 1, "content": text})
    doc.close()
    return pages


async def parse_docx(file_path: Path) -> list[dict]:
    """解析 DOCX 文件"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _parse_docx_sync, file_path)


def _parse_docx_sync(file_path: Path) -> list[dict]:
    """同步解析 DOCX

    每个非空段落视为一"页"（page = 段落序号+1）。不能只给 index：chunk_pages 用
    page 做 chunk_id 的唯一维度（chunk_index 每页重置为 0），若所有段落 page 都相同，
    多段落的第一个 chunk 会生成相同 chunk_id → Chroma DuplicateIDError → 摄取必失败。
    """
    from docx import Document

    doc = Document(str(file_path))
    paragraphs = []
    for i, para in enumerate(doc.paragraphs):
        if para.text.strip():
            paragraphs.append({"page": i + 1, "content": para.text})
    return paragraphs
