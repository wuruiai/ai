"""HyDE (Hypothetical Document Embedding)

假设性文档生成，用于提升检索效果。

Reference: EduAgent hyde_generate_node
"""

from backend.core.logger import get_logger
from backend.core.model_factory import ModelFactory

logger = get_logger(__name__)


class HyDEGenerator:
    """HyDE 生成器"""

    def __init__(self):
        self._llm = ModelFactory.create_llm(temperature=0.7)

    async def generate(self, query: str) -> str:
        """
        生成假设性文档。

        对于模糊查询，生成一个假设性的答案文档，用这个文档的向量去检索，
        比直接用模糊查询检索效果更好。

        Args:
            query: 用户查询（通常是模糊的）

        Returns:
            假设性文档文本
        """
        prompt = f"""你是一个水利行业专家。请根据以下问题，生成一段可能的答案文本。
这个文本不需要完全准确，但需要包含与问题相关的专业术语和概念。

用户问题：{query}

请生成一段 200-300 字的假设性答案："""

        try:
            response = await self._llm.ainvoke(prompt)
            hypothetical_doc = response.content.strip()
            logger.info("HyDE generated: %d chars", len(hypothetical_doc))
            return hypothetical_doc

        except Exception as e:  # noqa: BLE001 -- LLM 外部调用失败统一降级为原查询
            logger.warning("HyDE generation failed: %s", e)
            return query  # 降级：直接返回原查询


def get_hyde_generator() -> HyDEGenerator:
    """获取 HyDE 生成器单例"""
    return HyDEGenerator()
