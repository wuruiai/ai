"""置信度路由器

根据检索结果的置信度决定回答策略。

Reference: EduAgent QA graph confidence routing
"""

from enum import StrEnum
from typing import Any

from backend.core.logger import get_logger

logger = get_logger(__name__)

# 置信度阈值
HIGH_CONFIDENCE_THRESHOLD = 0.7
LOW_CONFIDENCE_THRESHOLD = 0.3


class ConfidenceLevel(StrEnum):
    """置信度级别"""

    HIGH = "high"  # 高置信度，用 RAG 回答
    MEDIUM = "medium"  # 中置信度，RAG + LLM 补充
    LOW = "low"  # 低置信度，直接用 LLM 回答


class ConfidenceRouter:
    """置信度路由器"""

    def evaluate(self, evidence: list[dict[str, Any]]) -> tuple[ConfidenceLevel, float]:
        """
        评估检索结果的置信度。

        Args:
            evidence: 检索结果列表

        Returns:
            (confidence_level, avg_score)
        """
        if not evidence:
            logger.info("No evidence found, confidence=low")
            return ConfidenceLevel.LOW, 0.0

        # 计算平均分数
        scores = [e.get("score", 0) for e in evidence]
        avg_score = sum(scores) / len(scores)

        # 根据平均分数判断置信度
        if avg_score >= HIGH_CONFIDENCE_THRESHOLD:
            level = ConfidenceLevel.HIGH
        elif avg_score >= LOW_CONFIDENCE_THRESHOLD:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW

        logger.info("Confidence evaluation: level=%s, avg_score=%.2f", level.value, avg_score)
        return level, avg_score


def get_confidence_router() -> ConfidenceRouter:
    """获取置信度路由器单例"""
    return ConfidenceRouter()
