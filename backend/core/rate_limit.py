"""每用户滑动窗口限流。

单进程用内存后端；设 `REDIS_URL` 后工厂返回 Redis 后端（跨进程共享计数）。
public 名字 `RateLimiter` 保留（即内存实现），模块单例 `rate_limiter` 走工厂。
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Depends, HTTPException

from backend.api.v1.auth import CurrentUser, get_current_user
from backend.config import settings
from backend.core.backends import (
    InMemoryRateLimitBackend,
    get_rate_limit_backend,
)

# 兼容旧引用：RateLimiter 即内存限流后端
RateLimiter = InMemoryRateLimitBackend

# 模块单例：REDIS_URL 非空时自动换 Redis 后端
rate_limiter = get_rate_limit_backend(settings.RATE_LIMIT_PER_MINUTE, 60)


def check_rate_limit(user: CurrentUser = Depends(get_current_user)) -> None:
    """FastAPI 依赖：按用户限流，超限抛 429。"""
    if not rate_limiter.allow(user.user_id):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


# ---------------------------------------------------------------------------
# 登录防爆破（G1.1）
# ---------------------------------------------------------------------------


class LoginThrottle:
    """登录失败锁定：窗口内失败次数超限后，该 key 锁定一段时间。

    key 取 `ip|username` 双维度：
      - ip 维度抗分布式暴力（防洪泛 401）
      - username 维度防单账户撞库
    权衡说明：按 username 锁定存在被攻击者恶意锁号的风险，故锁定时间短（默认 15 分钟）
    且 key 始终携带 ip 前缀，同一账户的锁定只影响该来源 IP 的登录尝试。
    """

    def __init__(self, max_failures: int, window_s: int, lockout_s: int):
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lockout_until: dict[str, float] = {}

    def check(self, key: str) -> None:
        """若已锁定则抛 429（带 Retry-After）；否则静默放行。"""
        now = time.monotonic()
        until = self._lockout_until.get(key, 0.0)
        if now < until:
            raise HTTPException(
                status_code=429,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": str(max(1, int(until - now)))},
            )

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        fails = self._failures[key]
        fails.append(now)
        # 只保留窗口内的失败
        self._failures[key] = [t for t in fails if now - t < self.window_s]
        if len(self._failures[key]) >= self.max_failures:
            self._lockout_until[key] = now + self.lockout_s

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._lockout_until.pop(key, None)


login_throttle = LoginThrottle(
    max_failures=settings.LOGIN_MAX_FAILURES,
    window_s=settings.LOGIN_WINDOW_S,
    lockout_s=settings.LOGIN_LOCKOUT_S,
)
