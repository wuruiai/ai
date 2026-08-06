"""多查询重写

将一个问题改写成多个角度的查询，提升检索召回率。

Reference: EduAgent multi_query_rewrite_node
"""

from backend.config import settings
from backend.core.logger import get_logger
from backend.core.model_factory import ModelFactory

logger = get_logger(__name__)


class MultiQueryRewriter:
    """多查询重写器"""

    def __init__(self):
        self._llm = ModelFactory.create_llm(temperature=0.7)

    async def rewrite(self, query: str, num_queries: int | None = None) -> list[str]:
        """
        将查询改写成多个角度。

        Args:
            query: 原始查询
            num_queries: 生成的查询数量（默认从配置读取）

        Returns:
            改写后的查询列表（包含原始查询）
        """
        num_queries = num_queries or settings.MAX_MULTI_QUERIES

        prompt = (
            "你是一个水利行业专家。请将以下问题改写成 "
            f"{num_queries} 个不同角度的问题，以便更好地检索相关文档。\n\n"
            f"原始问题：{query}\n\n"
            "要求：\n"
            "1. 每个问题都要保持原意，但从不同角度表述\n"
            "2. 可以使用同义词、不同句式、更具体/更抽象的表述\n"
            "3. 每行一个问题，不要编号\n\n"
            f"请输出 {num_queries} 个问题："
        )

        try:
            response = await self._llm.ainvoke(prompt)
            content = response.content.strip()

            # 解析生成的查询
            queries = [q.strip() for q in content.split("\n") if q.strip()]

            # 确保包含原始查询
            if query not in queries:
                queries.insert(0, query)

            # 限制数量
            queries = queries[:num_queries]

            logger.info("Multi-query rewrite: %d queries generated", len(queries))
            return queries

        except Exception as e:  # noqa: BLE001 -- LLM 外部调用失败统一降级为原查询
            logger.warning("Multi-query rewrite failed: %s", e)
            return [query]  # 降级：直接返回原查询


def get_multi_query_rewriter() -> MultiQueryRewriter:
    """获取多查询重写器单例"""
    return MultiQueryRewriter()
