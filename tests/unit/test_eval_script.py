"""RAG 评测脚本测试（G3.4）：eval_set 解析 / 指标计算 / 标题→id 解析 / 报告渲染。"""

from pathlib import Path

import pytest

from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from scripts import evaluate_rag


def test_parse_k_values():
    assert evaluate_rag.parse_k_values(["3,5", "10", "3"]) == [3, 5, 10]
    assert evaluate_rag.parse_k_values([]) == []


def test_load_eval_set(tmp_path: Path):
    p = tmp_path / "eval_set.jsonl"
    p.write_text(
        '{"question": "q1", "expected_docs": ["a"]}\n'
        "\n"
        '{"question": "q2", "expected_docs": ["b"]}\n',
        encoding="utf-8",
    )
    items = evaluate_rag.load_eval_set(p)
    assert len(items) == 2
    assert items[0]["question"] == "q1"


def test_compute_metrics_hit_recall_rr():
    expected = {"d1", "d2"}
    # 期望文档出现在第 2 位 → k=3 命中、recall=2/2、rr=1/2
    m = evaluate_rag.compute_metrics(["d9", "d2", "d1"], expected, k=3)
    assert m["hit"] is True
    assert m["recall"] == pytest.approx(1.0)
    assert m["rr"] == pytest.approx(0.5)
    # k=1 只看到 d9 → 未命中、recall=0、rr=0
    m1 = evaluate_rag.compute_metrics(["d9", "d2", "d1"], expected, k=1)
    assert m1["hit"] is False
    assert m1["recall"] == 0.0
    assert m1["rr"] == 0.0
    # 部分命中
    m2 = evaluate_rag.compute_metrics(["d1", "d9"], {"d1", "d2"}, k=2)
    assert m2["recall"] == pytest.approx(0.5)


async def test_resolve_expected_ids_from_titles():
    db = await get_connection()
    try:
        await migrate(db)
        await db.execute(
            "INSERT INTO documents "
            "(document_id, file_name, stored_path, file_hash, file_size, document_title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("doc-eval-1", "a.pdf", "uploads/a.pdf", "hash-a", 10, "防汛应急调度预案"),
        )
        await db.commit()
        resolved, missing = await evaluate_rag.resolve_expected_ids(
            db, ["防汛应急调度预案", "不存在的文档"]
        )
        assert resolved == ["doc-eval-1"]
        assert missing == ["不存在的文档"]
    finally:
        await close_db(db)


def test_render_report_aggregates():
    rows = [
        {
            "question": "q1",
            "expected_ids": ["d1"],
            "missing": [],
            "top_doc_ids": ["d1", "d9"],
            "answer_fragment": "",
        },
        {
            "question": "q2",
            "expected_ids": ["d2"],
            "missing": [],
            "top_doc_ids": ["d9", "d2"],
            "answer_fragment": "",
        },
    ]
    report = evaluate_rag.render_report(rows, [1, 2], user_id=None)
    assert "# RAG 检索质量评测报告" in report
    assert "评测条数: 2" in report
    # k=1：q1 命中、q2 未命中 → hit_rate 50%
    assert "| 1 | 50.00% |" in report
    # k=2：两条都命中 → hit_rate 100%
    assert "| 2 | 100.00% |" in report
    # 逐条明细有 q1/q2
    assert "q1" in report and "q2" in report


def test_eval_set_is_valid_jsonl():
    """tests/evaluation/eval_set.jsonl 每行都是合法 JSON，且含 required 字段。"""
    p = Path(__file__).resolve().parents[2] / "tests" / "evaluation" / "eval_set.jsonl"
    items = evaluate_rag.load_eval_set(p)
    assert len(items) >= 5, "评测集至少 5 条"
    for it in items:
        assert it.get("question")
        assert it.get("expected_docs"), "expected_docs 不能为空"
        assert it.get("answer_fragment")
        assert isinstance(it["expected_docs"], list)
