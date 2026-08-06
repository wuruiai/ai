"""每用户限流测试。"""

import time

from fastapi.testclient import TestClient

from backend.core.rate_limit import RateLimiter
from backend.main import app


def test_rate_limiter_blocks_after_limit():
    rl = RateLimiter(limit=3, window_s=60)
    assert all(rl.allow("u1") for _ in range(3))
    assert not rl.allow("u1")


def test_rate_limiter_window_slides():
    rl = RateLimiter(limit=1, window_s=1)
    assert rl.allow("u1")
    assert not rl.allow("u1")
    time.sleep(1.1)
    assert rl.allow("u1")  # 窗口滑动后恢复


def test_rate_limit_returns_429_envelope(monkeypatch):
    """限流触达时返回统一 429 envelope。"""

    class _Blocked:
        def allow(self, key):
            return False

    monkeypatch.setattr("backend.core.rate_limit.rate_limiter", _Blocked())
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/register", json={"username": "rl_user", "password": "pass123456"})
        tok = r.json()["token"]
        r = c.post(
            "/api/v1/chat/stream",
            json={"query": "x", "thread_id": "t"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "http_429"
