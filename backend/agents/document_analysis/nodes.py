"""分析节点

指定文档分析 Agent 节点：加载 → 提取结构 → 分析 → 摘要 → 组装。


流程：
    load_document     : 从指定文档（document_id）检索 chunks
    extract_structure : 按标题/段落规律提取章节结构（规则法，不耗 LLM）
    analyze_content   : LLM 分析内容与查询的相关信息
    generate_summary  : LLM 生成摘要
    finalize          : 组装关键点 + status
"""

from __future__ import annotations

import re
from typing import Any

from backend.agents.document_analysis.prompts import (
    CONTENT_ANALYSIS_PROMPT,
    KEY_POINTS_PROMPT,
    SUMMARY_PROMPT,
)
from backend.core.logger import get_logger
from backend.core.model_factory import ModelFactory
from backend.rag.retriever import retrieve as hybrid_retrieve

logger = get_logger(__name__)

# 标题模式：识别"第一章 / 第1章 / 1.  / 一、/ （一）"等章节标题
_HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百0-9]+[章节篇部]|"
    r"[0-9]+(?:\.[0-9]+)*[、.．]|"
    r"[一二三四五六七八九十]+[、．.])"
)


def _split_heading(content: str) -> list[dict]:
    """把文档文本按标题切成结构片段。返回 [{title, body}]"""
    blocks: list[dict] = []
    current_title = "概述"
    current_body: list[str] = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if _HEADING_RE.match(line) and len(line) < 60:
            if current_body:
                blocks.append({"title": current_title, "body": "\n".join(current_body)})
            current_title = line
            current_body = []
        else:
            current_body.append(line)
    if current_body or not blocks:
        blocks.append({"title": current_title, "body": "\n".join(current_body)})
    return blocks


async def load_document(state: dict[str, Any]) -> dict[str, Any]:
    """加载指定文档的 chunks。

    从 state 拿 document_id；用混合检索（document_id 过滤）拉取该文档前 N 个片段。
    """
    document_id = state.get("document_id") or ""
    query = state.get("query") or ""
    if not document_id:
        return {**state, "document_loaded": False, "status": "failed"}

    # 检索该文档的 top chunks（query 为空则用文档 ID 前缀做兜底查询）
    try:
        search_query = query or document_id[:8]
        results = await hybrid_retrieve(search_query, top_k=12, document_id=document_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("document_analysis load failed: %s", e)
        results = []

    chunks = [{"chunk_id": r.chunk_id, "content": r.content, "page": None} for r in results]
    if not chunks:
        return {
            **state,
            "document_loaded": False,
            "status": "failed",
            "summary": "未能加载指定文档的内容，请确认文档已导入且状态为 ready。",
        }

    return {
        **state,
        "document_loaded": True,
        "_chunks": chunks,
        "step_count": state.get("step_count", 0) + 1,
    }


async def extract_structure(state: dict[str, Any]) -> dict[str, Any]:
    """提取文档结构（规则法，不耗 LLM）。"""
    chunks = state.get("_chunks", [])
    full_text = "\n\n".join(c["content"] for c in chunks)
    blocks = _split_heading(full_text)
    structure = {
        "chunk_count": len(chunks),
        "sections": [{"title": b["title"], "length": len(b["body"])} for b in blocks[:20]],
    }
    return {**state, "structure": structure, "step_count": state.get("step_count", 0) + 1}


async def analyze_content(state: dict[str, Any]) -> dict[str, Any]:
    """LLM 分析：结合查询分析文档关键信息。"""
    chunks = state.get("_chunks", [])
    query = state.get("query") or ""
    document_text = "\n\n".join(c["content"][:500] for c in chunks[:8])
    if len(document_text) > 6000:
        document_text = document_text[:6000] + "……"

    try:
        llm = ModelFactory.create_llm(temperature=0.3, callbacks=state.get("llm_callbacks"))
        prompt = CONTENT_ANALYSIS_PROMPT.format(document=document_text, query=query)
        resp = await llm.ainvoke(prompt)
        analysis_text = resp.content
        analysis = {"text": analysis_text[:2000]}
    except Exception as e:  # noqa: BLE001
        logger.warning("analyze_content llm failed: %s", e)
        analysis = {"text": "（分析生成失败）"}

    return {
        **state,
        "analysis": analysis,
        "llm_call_count": state.get("llm_call_count", 0) + 1,
        "step_count": state.get("step_count", 0) + 1,
    }


async def generate_summary(state: dict[str, Any]) -> dict[str, Any]:
    """LLM 生成摘要 + 关键点。"""
    analysis = state.get("analysis", {})
    analysis_text = analysis.get("text", "")
    try:
        llm = ModelFactory.create_llm(temperature=0.3, callbacks=state.get("llm_callbacks"))
        summary_resp = await llm.ainvoke(SUMMARY_PROMPT.format(analysis=analysis_text))
        summary = summary_resp.content[:2000]

        points_resp = await llm.ainvoke(KEY_POINTS_PROMPT.format(analysis=analysis_text))
        key_points = [
            ln.strip("-• \n")
            for ln in points_resp.content.split("\n")
            if ln.strip() and not ln.strip().startswith(("#", "关键点", "要点"))
        ][:5]
    except Exception as e:  # noqa: BLE001
        logger.warning("generate_summary llm failed: %s", e)
        summary = "（摘要生成失败）"
        key_points = []

    return {
        **state,
        "summary": summary,
        "key_points": key_points,
        "llm_call_count": state.get("llm_call_count", 0) + 2,
        "step_count": state.get("step_count", 0) + 1,
    }


async def finalize(state: dict[str, Any]) -> dict[str, Any]:
    """组装最终输出（构造 messages 对齐 orchestrator 契约）。"""
    from langchain_core.messages import AIMessage

    summary = state.get("summary") or "（无摘要）"
    points = state.get("key_points") or []

    if not state.get("document_loaded"):
        status = "failed"
        answer = summary or "文档分析失败，请确认文档已导入且状态为 ready。"
    else:
        status = "done"
        points_text = "\n".join(f"- {p}" for p in points)
        sections = state.get("structure", {}).get("sections", [])
        structure_text = "；".join(f"{s['title']}({s['length']}字)" for s in sections[:10])
        chunk_count = state.get("structure", {}).get("chunk_count", 0)
        answer = (
            f"## 文档分析结果\n\n"
            f"**摘要**：{summary}\n\n"
            f"**关键点**：\n{points_text}\n\n"
            f"**章节结构**：{structure_text}\n"
            f"（共 {len(sections)} 个章节 / {chunk_count} 个片段）"
        )

    return {
        **state,
        "status": status,
        "messages": [AIMessage(content=answer)],
        "structured_output": {"status": status, "summary": summary, "key_points": points},
        "step_count": state.get("step_count", 0) + 1,
    }
