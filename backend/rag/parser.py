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


async def parse_txt(file_path: Path) -> list[dict]:
    """解析 TXT 文件：按行打页（每 50 行一页），读盘走线程池避免阻塞事件循环。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _parse_txt_sync, file_path)


def _parse_txt_sync(file_path: Path) -> list[dict]:
    """同步解析 TXT：按行打页（每 50 行一页），简单但稳定。"""
    text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    pages: list[dict] = []
    page_size = 50
    for i in range(0, len(lines), page_size):
        chunk_text = "\n".join(lines[i : i + page_size])
        if chunk_text.strip():
            pages.append({"page": len(pages) + 1, "content": chunk_text})
    if not pages:
        pages.append({"page": 1, "content": text})
    return pages


async def parse_md(file_path: Path) -> list[dict]:
    """解析 MD 文件：单页大文本（章节信息后续可加 heading 解析）。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _parse_md_sync, file_path)


def _parse_md_sync(file_path: Path) -> list[dict]:
    """同步解析 MD：当作单页大文本。"""
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return [{"page": 1, "content": text}]
