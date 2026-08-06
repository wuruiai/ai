"""WaterRAGError + 错误码族

自定义异常。

Reference: §4.7
"""


class WaterRAGError(Exception):
    """基础异常"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ConfigError(WaterRAGError):
    """配置错误"""

    def __init__(self, message: str):
        super().__init__("CONFIG_ERROR", message)


class ModelError(WaterRAGError):
    """模型调用错误"""

    def __init__(self, message: str):
        super().__init__("MODEL_ERROR", message)


class RetrievalError(WaterRAGError):
    """检索错误"""

    def __init__(self, message: str):
        super().__init__("RETRIEVAL_ERROR", message)


class DocumentError(WaterRAGError):
    """文档处理错误"""

    def __init__(self, message: str):
        super().__init__("DOCUMENT_ERROR", message)


class AgentError(WaterRAGError):
    """Agent 执行错误"""

    def __init__(self, message: str):
        super().__init__("AGENT_ERROR", message)


class RateLimitError(WaterRAGError):
    """限流错误"""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__("RATE_LIMIT_ERROR", message)
