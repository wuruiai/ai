"""查询分类器

使用 LLM 进行查询分类（云端方案，不需要本地模型）。

Reference: EduAgent query_classifier.py
"""

from enum import StrEnum

from backend.core.logger import get_logger
from backend.core.model_factory import ModelFactory

logger = get_logger(__name__)


class QueryType(StrEnum):
    """查询类型"""

    GENERAL = "general"  # 通用问题，可直接用 LLM 回答
    SPECIALIZED = "specialized"  # 专业问题，需要 RAG 检索


class QueryClassifier:
    """查询分类器（LLM 实现）"""

    _instance: "QueryClassifier | None" = None

    def __init__(self):
        self._llm = ModelFactory.create_llm(temperature=0)

    @classmethod
    def get_instance(cls) -> "QueryClassifier":
        """获取单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def classify(self, query: str) -> tuple[QueryType, float]:
        """
        分类查询类型。

        Args:
            query: 用户查询

        Returns:
            (query_type, confidence)
        """
        prompt = f"""请判断以下用户问题的类型：

用户问题：{query}

分类规则：
- general：通用常识问题、闲聊、不涉及水利专业知识的问题
- specialized：涉及水利行业专业知识、需要查阅水利文档的问题

请只返回一个 JSON：{{"type": "general 或 specialized", "confidence": 0.0-1.0}}"""

        try:
            response = await self._llm.ainvoke(prompt)
            content = response.content.strip()

            # 解析 JSON
            import json

            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]

            result = json.loads(content)
            query_type = QueryType(result.get("type", "specialized"))
            confidence = float(result.get("confidence", 0.5))

            logger.info("Query classified: %s, confidence=%.2f", query_type.value, confidence)
            return query_type, confidence

        except Exception as e:  # noqa: BLE001 -- LLM 外部调用失败统一降级为 specialized
            logger.warning("Query classification failed: %s, defaulting to specialized", e)
            return QueryType.SPECIALIZED, 0.5


def get_query_classifier() -> QueryClassifier:
    """获取查询分类器单例"""
    return QueryClassifier.get_instance()
