"""可插拔限流/预算后端测试（G5.3）。

用 FakeRedis 验证 Redis 实现的窗口/预算逻辑，无需真实 Redis。
"""

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.core import backends
from backend.core.backends import (
    InMemoryBudgetBackend,
    InMemoryRateLimitBackend,
    RedisBudgetBackend,
    RedisRateLimitBackend,
    get_budget_backend,
    get_rate_limit_backend,
)

# ---------------------------------------------------------------------------
# FakeRedis：sorted set + string 的最小实现
# ---------------------------------------------------------------------------


class _FakePipe:
    def __init__(self, r: "_FakeRedis") -> None:
        self._r = r
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key, min_, max_):
        self._ops.append(("zrem", key, min_, max_))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def execute(self) -> list:
        out = []
        for op in self._ops:
            if op[0] == "zrem":
                out.append(self._r.zremrangebyscore(op[1], op[2], op[3]))
            elif op[0] == "zcard":
                out.append(self._r.zcard(op[1]))
        self._ops.clear()
        return out


class _FakeRedis:
    """最小可用的 Redis 假实现，够测限流/预算逻辑。"""

    def __init__(self) -> None:
        self._zsets: dict[str, dict] = {}
        self._str: dict[str, str] = {}

    # --- pipeline ---
    def pipeline(self, transaction=True):
        return _FakePipe(self)

    # --- zset ---
    def zadd(self, key, mapping):
        self._zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key, min_, max_):
        zs = self._zsets.get(key, {})
        dead = [m for m, s in zs.items() if min_ <= s <= max_]
        for m in dead:
            del zs[m]
        return len(dead)

    def zcard(self, key):
        return len(self._zsets.get(key, {}))

    # --- string ---
    def get(self, key):
        return self._str.get(key)

    def incr(self, key):
        v = int(self._str.get(key, "0")) + 1
        self._str[key] = str(v)
        return v

    # --- ttl（仅记录，不实际过期） ---
    def expire(self, key, ttl):
        return True


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def test_factory_default_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    assert isinstance(get_rate_limit_backend(10, 60), InMemoryRateLimitBackend)
    assert isinstance(get_budget_backend(), InMemoryBudgetBackend)


def test_factory_redis_when_url_set(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379")
    monkeypatch.setattr(backends, "_redis_client", lambda: fake)
    assert isinstance(get_rate_limit_backend(10, 60), RedisRateLimitBackend)
    assert isinstance(get_budget_backend(), RedisBudgetBackend)


# ---------------------------------------------------------------------------
# 限流：内存
# ---------------------------------------------------------------------------


def test_in_memory_rate_limit_blocks_after_limit():
    rl = InMemoryRateLimitBackend(limit=3, window_s=60)
    assert all(rl.allow("u1") for _ in range(3))
    assert not rl.allow("u1")


def test_in_memory_rate_limit_window_slides():
    rl = InMemoryRateLimitBackend(limit=1, window_s=1)
    assert rl.allow("u1")
    assert not rl.allow("u1")


# ---------------------------------------------------------------------------
# 限流：Redis
# ---------------------------------------------------------------------------


def test_redis_rate_limit_sliding_window(monkeypatch):
    """窗口内 limit 次放行，第 limit+1 次拒绝；窗口滑动后恢复。"""
    clock = {"t": 1_000_000.0}

    def _fake_time():
        return clock["t"]

    monkeypatch.setattr("backend.core.backends.time.time", _fake_time)
    rl = RedisRateLimitBackend(limit=3, window_s=60, client=_FakeRedis())
    assert all(rl.allow("u1") for _ in range(3))
    assert not rl.allow("u1")

    # 窗口滑动 60s+：旧记录过期，恢复放行
    clock["t"] += 61
    assert rl.allow("u1")


# ---------------------------------------------------------------------------
# 预算：内存 / Redis
# ---------------------------------------------------------------------------


def test_budget_in_memory_under_and_over(monkeypatch):
    bm = InMemoryBudgetBackend()
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bm.check_budget()  # 0 < 2 不抛
    bm.record_call("qwen-plus")
    bm.record_call("qwen-max")
    with pytest.raises(HTTPException) as ei:
        bm.check_budget()  # 2 >= 2 → 429
    assert ei.value.status_code == 429


def test_budget_redis_shared_counter(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bb = RedisBudgetBackend(client=_FakeRedis())
    bb.check_budget()  # 0 < 2 不抛
    bb.record_call("qwen-plus")
    bb.record_call("qwen-plus")
    with pytest.raises(HTTPException) as ei:
        bb.check_budget()  # 2 >= 2 → 429
    assert ei.value.status_code == 429
