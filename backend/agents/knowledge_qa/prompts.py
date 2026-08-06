"""Prompt 常量 + PROMPT_VERSION

知识库问答 Agent 提示词。

Reference: §8.2, EduAgent QA prompts
"""

PROMPT_VERSION = "1.0.0"

QUERY_PARSE_PROMPT = """你是一个水利行业专家。请分析用户的查询，提取关键信息。

用户查询: {query}

请输出:
1. 核心问题
2. 关键词
3. 查询意图
"""

MULTI_QUERY_PROMPT = """基于以下查询，生成 {n} 个不同角度的查询，用于检索相关文档。

原始查询: {query}

请生成查询列表:
"""

RELEVANCE_CHECK_PROMPT = """判断以下证据是否与查询相关。

查询: {query}
证据: {evidence}

请回答: 相关/不相关
"""

ANSWER_PROMPT = """你是水利行业知识问答助手。请基于以下证据回答用户问题。

查询: {query}
证据: {evidence}

要求:
1. 回答要准确、专业
2. 引用证据时标注来源 [1][2]
3. 如果证据不足，明确说明

请回答:
"""

QUALITY_CHECK_PROMPT = """检查以下答案的质量。

查询: {query}
答案: {answer}

请评估:
1. 是否回答了问题
2. 引用是否准确
3. 是否有遗漏

请回答: 通过/需要优化
"""

REFINE_PROMPT = """优化以下答案，使其更准确、完整。

原始查询: {query}
当前答案: {answer}
优化建议: {suggestion}

请输出优化后的答案:
"""

HYDE_PROMPT = """你是一个水利行业专家。请根据以下问题，生成一段可能的答案文档。
这个文档不需要完全准确，但需要包含与问题相关的专业术语和概念。

用户问题: {query}

请生成一段 200-300 字的假设性文档:
"""
