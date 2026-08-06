"""每用户滑动窗口限流。

内存实现（单进程够用）；多 worker 部署时建议换 Redis。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.config import settings


class RateLimiter:
    def __init__(self, limit: int = 30, window_s: int = 60):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] >= self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


rate_limiter = RateLimiter(limit=settings.RATE_LIMIT_PER_MINUTE, window_s=60)


def check_rate_limit(user: CurrentUser = Depends(get_current_user)) -> None:
    """FastAPI 依赖：按用户限流，超限抛 429。"""
    if not rate_limiter.allow(user.user_id):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
