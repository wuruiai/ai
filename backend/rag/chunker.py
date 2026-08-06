"""两阶段切分（结构→长度）

文档切分器。

Reference: §5.5
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_text(text: str, chunk_size: int = 512, chunk_overlap: int = 64) -> list[str]:
    """切分文本"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )
    return splitter.split_text(text)


def chunk_pages(pages: list[dict], chunk_size: int = 512) -> list[dict]:
    """切分页面"""
    chunks = []
    for page in pages:
        text = page.get("content", "")
        page_num = page.get("page", 1)
        page_chunks = chunk_text(text, chunk_size)
        for i, chunk in enumerate(page_chunks):
            chunks.append(
                {
                    "content": chunk,
                    "page": page_num,
                    "chunk_index": i,
                }
            )
    return chunks
