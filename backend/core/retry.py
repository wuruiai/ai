"""三层兜底重试

重试策略封装。

Reference: §4.4
"""

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.config import settings


def create_retry_decorator():
    """创建重试装饰器"""
    return retry(
        stop=stop_after_attempt(settings.MAX_RETRIES + 1),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
