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


class _FakeRedis:
    """最小可用的 Redis 假实现，够测限流/预算逻辑。"""

    def __init__(self) -> None:
        self._zsets: dict[str, dict] = {}
        self._str: dict[str, str] = {}

    # --- eval（复刻 _REDIS_ALLOW_LUA：清过期→计数→条件写入，单次原子） ---
    # args = [key, min_ms, limit, score, member, window_s]
    def eval(self, script, numkeys, *args):
        key, min_ms, limit = args[0], int(args[1]), int(args[2])
        self.zremrangebyscore(key, 0, min_ms)
        if self.zcard(key) >= limit:
            return 0
        self.zadd(key, {args[4]: int(args[3])})
        self.expire(key, int(args[5]))
        return 1

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


def test_redis_rate_limit_single_atomic_eval_roundtrip(monkeypatch):
    """G10.24：allow 通过单次原子 eval 完成，不再"pipeline 计数 + 客户端另发 zadd"两步走。

    两次请求间没有任何客户端往返窗口，多 worker 并发突发时在 Redis 侧原子串行，
    不会双双读到 count=limit-1 而突破上限。
    """
    fake = _FakeRedis()
    calls = {"n": 0}
    real_eval = fake.eval

    def _counting_eval(script, numkeys, *args):
        calls["n"] += 1
        return real_eval(script, numkeys, *args)

    fake.eval = _counting_eval
    rl = RedisRateLimitBackend(limit=3, window_s=60, client=fake)
    for _ in range(3):
        assert rl.allow("u1") is True
    assert rl.allow("u1") is False
    assert calls["n"] == 4  # 每次 allow 恰好一次 eval，无额外 pipeline/zadd 往返


# ---------------------------------------------------------------------------
# 预算：内存 / Redis
# ---------------------------------------------------------------------------


def test_budget_in_memory_under_and_over(monkeypatch):
    bm = InMemoryBudgetBackend()
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bm.check_budget("u1")  # 0 < 2 不抛
    bm.record_call("u1", "qwen-plus")
    bm.record_call("u1", "qwen-max")
    with pytest.raises(HTTPException) as ei:
        bm.check_budget("u1")  # 2 >= 2 → 429
    assert ei.value.status_code == 429


def test_budget_in_memory_per_user_isolation(monkeypatch):
    """内存预算按用户隔离：A 超限不影响 B。"""
    bm = InMemoryBudgetBackend()
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bm.record_call("alice", "qwen-plus")
    bm.record_call("alice", "qwen-max")
    with pytest.raises(HTTPException):
        bm.check_budget("alice")
    bm.check_budget("bob")  # 不同用户不受影响
    bm.record_call("bob", "qwen-plus")
    assert bm._daily_calls["bob"]["qwen-plus"] == 1


def test_budget_redis_shared_counter(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bb = RedisBudgetBackend(client=_FakeRedis())
    bb.check_budget("u1")  # 0 < 2 不抛
    bb.record_call("u1", "qwen-plus")
    bb.record_call("u1", "qwen-plus")
    with pytest.raises(HTTPException) as ei:
        bb.check_budget("u1")  # 2 >= 2 → 429
    assert ei.value.status_code == 429


def test_budget_redis_per_user_isolation(monkeypatch):
    """Redis 预算 key 含 user_id：A 超限不影响 B。"""
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bb = RedisBudgetBackend(client=_FakeRedis())
    bb.record_call("alice", "qwen-plus")
    bb.record_call("alice", "qwen-plus")
    with pytest.raises(HTTPException):
        bb.check_budget("alice")
    bb.check_budget("bob")  # 不同用户 key 不共享计数
    bb.record_call("bob", "qwen-plus")
    assert bb._redis.get(f"budget:{bb._date()}:bob") == "1"
