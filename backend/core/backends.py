"""可插拔的限流/预算后端（G5.3）

单进程部署用内存实现（默认）；多实例/多 worker 部署时设 `REDIS_URL` 即自动
切换 Redis 实现，计数跨进程共享。

抽象：
    RateLimitBackend.allow(key) -> bool   滑动窗口限流
    BudgetBackend.check_budget(user_id)/record_call(user_id, model)
                                         每日调用预算（按用户计数）

工厂：
    get_rate_limit_backend(limit, window_s)
    get_budget_backend()
    —— 读 settings.REDIS_URL，非空则返回 Redis 实现（redis 包按需懒加载，
       未安装时给出明确报错，不拖垮默认路径）。
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import date
from typing import Protocol

from fastapi import HTTPException

from backend.config import settings

# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------


class RateLimitBackend(Protocol):
    def allow(self, key: str) -> bool: ...


class BudgetBackend(Protocol):
    def check_budget(self, user_id: str) -> None: ...
    def record_call(self, user_id: str, model: str) -> None: ...


# ---------------------------------------------------------------------------
# 限流：内存实现
# ---------------------------------------------------------------------------


class InMemoryRateLimitBackend:
    """每 key 滑动窗口限流（单进程内存，deque 存时间戳）。"""

    def __init__(self, limit: int, window_s: int) -> None:
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


# ---------------------------------------------------------------------------
# 限流：Redis 实现（滑动窗口，sorted set）
# ---------------------------------------------------------------------------


class RedisRateLimitBackend:
    """Redis 滑动窗口限流。

    key = `rl:{key}`，sorted set 存窗口内时间戳；每次 allow 清理过期后计数。
    与 InMemory 语义一致：窗口内允许 limit 次，第 limit+1 次拒绝。
    """

    def __init__(self, limit: int, window_s: int, client: object | None = None) -> None:
        self.limit = limit
        self.window_s = window_s
        self._redis = client if client is not None else _redis_client()

    def allow(self, key: str) -> bool:
        rkey = f"rl:{key}"
        now_ms = int(time.time() * 1000)
        min_ms = now_ms - self.window_s * 1000
        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(rkey, 0, min_ms)
        pipe.zcard(rkey)
        count = pipe.execute()[1]
        if count >= self.limit:
            return False
        # member 用唯一值、score 用时间戳：同一毫秒内的多次请求不会合并成一条
        self._redis.zadd(rkey, {str(uuid.uuid4()): now_ms})
        self._redis.expire(rkey, self.window_s)
        return True


# ---------------------------------------------------------------------------
# 预算：内存实现
# ---------------------------------------------------------------------------


class InMemoryBudgetBackend:
    """每日调用预算（单进程内存；按用户计数，换天自动清零）。

    `_daily_calls[user_id][model]` 记录每用户每模型的调用次数；
    `DAILY_CALL_LIMIT` 是每用户每日总调用上限。
    """

    def __init__(self) -> None:
        self._daily_calls: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._current_date: date = date.today()

    def check_budget(self, user_id: str) -> None:
        today = date.today()
        if today != self._current_date:
            self._daily_calls.clear()
            self._current_date = today

        # .get 读取，避免 check 时把未调用过的用户写进计数表
        total = sum(self._daily_calls.get(user_id, {}).values())
        if total >= settings.DAILY_CALL_LIMIT:
            raise HTTPException(status_code=429, detail="Daily call limit exceeded")

    def record_call(self, user_id: str, model: str) -> None:
        self._daily_calls[user_id][model] += 1


# ---------------------------------------------------------------------------
# 预算：Redis 实现（每日计数 key）
# ---------------------------------------------------------------------------


class RedisBudgetBackend:
    """Redis 每日调用预算（key = `budget:{date}:{user_id}`，TTL 1 天）。

    按用户隔离计数，多 worker/多实例共享；`DAILY_CALL_LIMIT` 是每用户每日上限。
    """

    def __init__(self, client: object | None = None) -> None:
        self._redis = client if client is not None else _redis_client()

    @staticmethod
    def _date() -> str:
        return date.today().isoformat()

    @staticmethod
    def _key(date_str: str, user_id: str) -> str:
        return f"budget:{date_str}:{user_id}"

    def check_budget(self, user_id: str) -> None:
        total = int(self._redis.get(self._key(self._date(), user_id)) or 0)
        if total >= settings.DAILY_CALL_LIMIT:
            raise HTTPException(status_code=429, detail="Daily call limit exceeded")

    def record_call(self, user_id: str, model: str) -> None:
        key = self._key(self._date(), user_id)
        self._redis.incr(key)
        self._redis.expire(key, 86400)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def _redis_client():
    """按 REDIS_URL 创建 redis 客户端（懒加载：无 redis 包时给出明确报错）。"""
    try:
        import redis
    except ImportError as e:  # pragma: no cover -- 依赖缺失时才走这里
        raise RuntimeError("REDIS_URL 已设置但未安装 redis 包：`pip install redis`") from e
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_rate_limit_backend(limit: int, window_s: int) -> RateLimitBackend:
    """限流后端工厂：REDIS_URL 非空 → Redis，否则内存。"""
    if settings.REDIS_URL:
        return RedisRateLimitBackend(limit, window_s)
    return InMemoryRateLimitBackend(limit, window_s)


def get_budget_backend() -> BudgetBackend:
    """预算后端工厂：REDIS_URL 非空 → Redis，否则内存。"""
    if settings.REDIS_URL:
        return RedisBudgetBackend()
    return InMemoryBudgetBackend()
