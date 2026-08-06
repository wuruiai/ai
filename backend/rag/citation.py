"""引用校验（CitationChecker）

引用验证工具。

Reference: §7.3
"""


class CitationChecker:
    """引用检查器"""

    def verify_citation(self, answer: str, citations: list[dict]) -> list[dict]:
        """验证引用"""
        verified = []
        for citation in citations:
            # TODO: 实现引用验证逻辑
            # 检查引用是否在答案中被正确引用
            verified.append(
                {
                    **citation,
                    "verified": True,
                }
            )
        return verified

    def format_citation(self, index: int, source: str, page: int) -> str:
        """格式化引用"""
        return f"[{index}] {source} (p.{page})"


citation_checker = CitationChecker()
