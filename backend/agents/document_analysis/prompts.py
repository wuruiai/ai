"""分析专用 Prompt

指定文档分析 Agent 提示词。

"""

PROMPT_VERSION = "1.0.0"

STRUCTURE_EXTRACTION_PROMPT = """分析以下文档的结构。

文档内容: {content}

请提取:
1. 章节结构
2. 关键段落
3. 表格/图表位置

请以 JSON 格式输出。
"""

CONTENT_ANALYSIS_PROMPT = """分析以下文档内容。

文档: {document}
查询: {query}

请分析:
1. 与查询相关的关键信息
2. 重要数据和结论
3. 引用位置

请输出分析结果。
"""

SUMMARY_PROMPT = """生成以下文档的摘要。

文档分析: {analysis}

要求:
1. 简洁明了
2. 突出重点
3. 包含关键数据

请输出摘要。
"""

KEY_POINTS_PROMPT = """提取以下文档的关键点。

文档分析: {analysis}

请列出 3-5 个关键点:
"""
