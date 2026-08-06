"""置信度路由器

根据检索结果的置信度决定回答策略。

分数域说明（G3.3 修复的背景，重要）：
- `retriever` 的融合分是**查询内 min-max 归一化**的（top dense=1.0），
  单一路径命中时 fused 分最高仅 `DENSE_WEIGHT=0.7`（见 agents/knowledge_qa/nodes.py 注释），
  因此跨查询的绝对值不可比，固定绝对阈值（旧 HIGH=0.7）几乎不可达。
- 改用**顶分 + 领先 margin** 双条件：顶分足够高，且明显高于次高（证据"唯一且强烈"）→ HIGH。
  这是 min-max 归一化下更稳的相对置信度信号。

Reference: EduAgent QA graph confidence routing
"""

from enum import StrEnum
from typing import Any

from backend.core.logger import get_logger

logger = get_logger(__name__)

# 置信度阈值（相对信号，见模块 docstring）
HIGH_TOP_THRESHOLD = 0.6  # HIGH 要求：顶分 ≥ 0.6
HIGH_MARGIN_THRESHOLD = 0.1  # HIGH 要求：顶分 − 次高分 ≥ 0.1（证据唯一且强烈）
MEDIUM_TOP_THRESHOLD = 0.35  # MEDIUM 要求：顶分 ≥ 0.35（有可用证据，但不够领先）


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
            evidence: 检索结果列表（每项需含 "score" 融合分）

        Returns:
            (confidence_level, avg_score)
        """
        if not evidence:
            logger.info("No evidence found, confidence=low")
            return ConfidenceLevel.LOW, 0.0

        scores = sorted((e.get("score", 0) for e in evidence), reverse=True)
        top = scores[0]
        avg_score = sum(scores) / len(scores)

        # 顶分与次高分的差距：唯一且强烈的证据 → 高置信。
        # 单条证据时无"次高"可比，以顶分本身作为领先信号。
        margin = top if len(scores) < 2 else top - scores[1]

        if top >= HIGH_TOP_THRESHOLD and margin >= HIGH_MARGIN_THRESHOLD:
            level = ConfidenceLevel.HIGH
        elif top >= MEDIUM_TOP_THRESHOLD:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW

        logger.info(
            "Confidence evaluation: level=%s, top=%.2f, margin=%.2f, avg=%.2f",
            level.value,
            top,
            margin,
            avg_score,
        )
        return level, avg_score


# 单例（模块级，避免每次调用都 new 新实例——旧实现名为单例实为新实例）
_router_instance: ConfidenceRouter | None = None


def get_confidence_router() -> ConfidenceRouter:
    """获取置信度路由器单例"""
    global _router_instance
    if _router_instance is None:
        _router_instance = ConfidenceRouter()
    return _router_instance
