"""引用校验（CitationChecker）

对"答案是否真的用到了引用来源"做可解释的机器校验（G3.2，防幻觉）。

原理：把答案与每条引用的内容归一化后按字符 n-gram 切分，
计算引用的 n-gram 有多少出现在答案里（coverage）。
coverage 达标 → 该引用"已核实"（verified=True）；否则标记"待核实"，
前端用不同样式提示用户——该引用可能是 LLM 自说自话，需要人工复核。

"""

from __future__ import annotations

import re

# 2-gram 兼顾中文（bigram 是中文子串命中最稳的粒度）与拉丁词
NGRAM_SIZE = 2
# 引用片段约 1/6 以上的内容进了答案才算"用到了"（保守，宁缺毋滥）
CITATION_COVERAGE_THRESHOLD = 0.15

# 归一化：只保留词字符（含 CJK），标点/空白/emoji 一律剔除
_NON_WORD = re.compile(r"[^\w一-鿿]", re.UNICODE)


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", text).lower()


def _ngrams(text: str, n: int = NGRAM_SIZE) -> set[str]:
    """重叠字符 n-gram 集合；长度不足 n 时整串作为一个 token。"""
    text = _normalize(text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


class CitationChecker:
    """引用检查器"""

    def verify_citation(self, answer: str, citations: list[dict]) -> list[dict]:
        """逐条验证引用是否被答案覆盖。

        - 答案为空 / 引用内容为空 → verified=False（无从验证）
        - coverage = |answer_ngrams ∩ citation_ngrams| / |citation_ngrams|
        - verified = coverage >= CITATION_COVERAGE_THRESHOLD
        - 返回值在原 citation 基础上追加 verified / coverage 字段
        """
        answer_ngrams = _ngrams(answer or "")
        verified_citations = []
        for citation in citations:
            src_ngrams = _ngrams(citation.get("content") or "")
            if not src_ngrams or not answer_ngrams:
                coverage = 0.0
            else:
                coverage = len(src_ngrams & answer_ngrams) / len(src_ngrams)
            verified_citations.append(
                {
                    **citation,
                    "verified": coverage >= CITATION_COVERAGE_THRESHOLD,
                    "coverage": round(coverage, 3),
                }
            )
        return verified_citations

    def format_citation(self, index: int, source: str, page: int) -> str:
        """格式化引用"""
        return f"[{index}] {source} (p.{page})"


citation_checker = CitationChecker()
