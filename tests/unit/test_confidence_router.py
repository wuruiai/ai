"""置信度路由测试（G3.3）：HIGH / MEDIUM / LOW 三档 + 真单例。"""

import pytest

from backend.core.confidence_router import (
    ConfidenceLevel,
    ConfidenceRouter,
    get_confidence_router,
)


def _evidence(*scores: float) -> list[dict]:
    return [{"chunk_id": f"c{i}", "content": "x", "score": s} for i, s in enumerate(scores)]


def test_empty_evidence_is_low():
    level, avg = ConfidenceRouter().evaluate([])
    assert level == ConfidenceLevel.LOW
    assert avg == 0.0


def test_strong_unique_evidence_is_high():
    """顶分高且显著领先次高 → HIGH（旧阈值 0.7 下这类单一路径 fused 分不可达）。"""
    level, avg = ConfidenceRouter().evaluate(_evidence(0.95, 0.40, 0.35))
    assert level == ConfidenceLevel.HIGH
    assert avg == pytest.approx((0.95 + 0.40 + 0.35) / 3)


def test_single_high_evidence_is_high():
    """单条证据：顶分本身即领先信号。"""
    level, _ = ConfidenceRouter().evaluate(_evidence(0.9))
    assert level == ConfidenceLevel.HIGH


def test_high_top_but_no_margin_is_medium():
    """顶分够高但多条证据分数接近（无领先）→ MEDIUM 而非 HIGH。"""
    level, _ = ConfidenceRouter().evaluate(_evidence(0.65, 0.60, 0.55))
    assert level == ConfidenceLevel.MEDIUM


def test_single_moderate_evidence_is_medium():
    level, _ = ConfidenceRouter().evaluate(_evidence(0.5))
    assert level == ConfidenceLevel.MEDIUM


def test_low_top_is_low():
    level, _ = ConfidenceRouter().evaluate(_evidence(0.30, 0.20))
    assert level == ConfidenceLevel.LOW


def test_get_confidence_router_is_singleton():
    assert get_confidence_router() is get_confidence_router()
    assert isinstance(get_confidence_router(), ConfidenceRouter)
