"""RAG 检索质量评测（G3.4）

对 `tests/evaluation/eval_set.jsonl` 中每条问题跑统一混合检索（**不调用 LLM**，只跑检索），
计算 recall@k / hit_rate@k / MRR@k，输出 Markdown 报告并固化基线。

Usage:
    python -m scripts.evaluate_rag   # 默认 tests/evaluation/eval_set.jsonl → 同目录报告
    python -m scripts.evaluate_rag --k 3 --k 5        # 自定义 top-k（默认 3,5,10）
    python -m scripts.evaluate_rag --user-id alice    # 限定用户数据（默认全量检索）

注意：
    - 依赖 DashScope embedding（检索需要向量化），但全程不调 LLM，可在开发机离线跑
    - eval_set 用 `expected_docs`（文档标题）标注期望命中；脚本运行时把标题解析为真实
      document_id。标题未入库会输出 WARN，该条按未命中计——便于发现"评测文档还没上传"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(ROOT))

from backend.db.connection import close_db, close_pool, get_connection  # noqa: E402
from backend.rag.retriever import retrieve  # noqa: E402

DEFAULT_EVAL_SET = ROOT / "tests" / "evaluation" / "eval_set.jsonl"
DEFAULT_REPORT = ROOT / "tests" / "evaluation" / "rag-eval-report.md"


def load_eval_set(path: Path) -> list[dict]:
    """读取 jsonl（每行一个 JSON 对象，跳过空行）。"""
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def parse_k_values(raw: list[str]) -> list[int]:
    """把 '--k 3,5,10' 之类解析成 [3,5,10]，去重排序。"""
    ks: set[int] = set()
    for part in raw:
        for token in part.split(","):
            token = token.strip()
            if token:
                ks.add(int(token))
    return sorted(ks)


async def resolve_expected_ids(db, titles: list[str]) -> tuple[list[str], list[str]]:
    """把文档标题解析为 document_id；返回 (resolved_ids, missing_titles)。"""
    resolved: list[str] = []
    missing: list[str] = []
    for title in titles:
        async with db.execute(
            "SELECT document_id FROM documents WHERE document_title=? LIMIT 1", (title,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            resolved.append(row[0])
        else:
            missing.append(title)
    return resolved, missing


def compute_metrics(top_doc_ids: list[str], expected: set[str], k: int) -> dict:
    """单条问题、单 k 的指标：hit / recall / reciprocal_rank。"""
    topk = top_doc_ids[:k]
    hits = [i for i, d in enumerate(topk) if d in expected]
    hit = len(hits) > 0
    recall = len({d for d in topk if d in expected}) / len(expected) if expected else 0.0
    rr = 1.0 / (hits[0] + 1) if hits else 0.0
    return {"hit": hit, "recall": recall, "rr": rr}


async def run_eval(items: list[dict], k_values: list[int], user_id: str | None) -> list[dict]:
    """对每条问题跑检索，附上命中信息；返回行数据。"""
    db = await get_connection()
    try:
        rows = []
        for item in items:
            question = item["question"]
            expected_ids, missing = await resolve_expected_ids(db, item.get("expected_docs", []))
            if missing:
                print(f"  [WARN] 评测文档未入库: {missing}  <-  {question[:24]}…")
            results = await retrieve(question, top_k=max(k_values), user_id=user_id)
            rows.append(
                {
                    "question": question,
                    "expected_ids": expected_ids,
                    "missing": missing,
                    "top_doc_ids": [r.document_id for r in results],
                    "answer_fragment": item.get("answer_fragment", ""),
                }
            )
        return rows
    finally:
        await close_db(db)
        # G10.24：一次性脚本收尾关闭连接池，避免 aiosqlite 后台线程拖住解释器
        await close_pool()


def render_report(rows: list[dict], k_values: list[int], user_id: str | None) -> str:
    """渲染 Markdown 报告（含聚合指标与逐条明细）。"""
    n = max(1, len(rows))
    lines = [
        "# RAG 检索质量评测报告",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 评测条数: {len(rows)}",
        f"- 检索范围: {'全部文档' if not user_id else f'用户 {user_id}'}",
        "",
        "## 聚合指标",
        "",
        "| k | hit_rate@k | recall@k | MRR@k |",
        "|---|------------|----------|-------|",
    ]
    for k in k_values:
        hits = 0
        recall_sum = 0.0
        rr_sum = 0.0
        for r in rows:
            m = compute_metrics(r["top_doc_ids"], set(r["expected_ids"]), k)
            hits += 1 if m["hit"] else 0
            recall_sum += m["recall"]
            rr_sum += m["rr"]
        lines.append(f"| {k} | {hits / n:.2%} | {recall_sum / n:.2%} | {rr_sum / n:.3f} |")

    lines += [
        "",
        "## 逐条明细",
        "",
        "| # | 问题 | 期望文档命中 | 说明 |",
        "|---|------|--------------|------|",
    ]
    for i, r in enumerate(rows, start=1):
        expected = set(r["expected_ids"])
        topk = r["top_doc_ids"][: k_values[0]]
        hit_str = "、".join(r["expected_ids"]) if r["expected_ids"] else "（未配置）"
        note = "✅ 命中" if expected and expected & set(topk) else ("⚠️ 未命中" if expected else "—")
        lines.append(f"| {i} | {r['question']} | {hit_str} | {note} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET))
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--k", action="append", default=[], help="top-k，默认 3,5,10")
    parser.add_argument("--user-id", default=None)
    args = parser.parse_args()

    k_values = parse_k_values(args.k) or [3, 5, 10]
    items = load_eval_set(Path(args.eval_set))
    print(f"loaded {len(items)} eval items, k={k_values}")

    rows = asyncio.run(run_eval(items, k_values, args.user_id))
    report = render_report(rows, k_values, args.user_id)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"report written: {args.out}")
    hit_rate = sum(
        1
        for r in rows
        if compute_metrics(r["top_doc_ids"], set(r["expected_ids"]), k_values[0])["hit"]
    ) / max(1, len(rows))
    print(f"hit_rate@{k_values[0]} = {hit_rate:.2%}")


if __name__ == "__main__":
    main()
