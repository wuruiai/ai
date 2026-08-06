"""会话与反馈 API 测试。"""

import sqlite3
import uuid

from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app


def _register(c: TestClient, name: str) -> tuple[str, str]:
    r = c.post("/api/v1/auth/register", json={"username": name, "password": "pass123456"})
    body = r.json()
    return body["token"], body["user"]["user_id"]


def _insert_message(thread_id: str, user_id: str) -> str:
    """同步播种一条消息（用 stdlib sqlite3，避免 asyncio.run 与 TestClient 循环纠缠）。"""
    mid = str(uuid.uuid4())
    conn = sqlite3.connect(settings.SQLITE_PATH)
    try:
        conn.execute(
            "INSERT INTO messages (message_id, thread_id, role, content, user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, thread_id, "user", "水利工程是什么", user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return mid


def test_threads_and_feedback():
    with TestClient(app) as c:
        tok, uid = _register(c, "tf_user")
        h = {"Authorization": f"Bearer {tok}"}

        # 空会话列表
        r = c.get("/api/v1/threads/", headers=h)
        assert r.status_code == 200 and r.json()["total"] == 0

        # 插入一条消息（模拟历史会话）
        thread_id = "th_" + uuid.uuid4().hex[:8]
        mid = _insert_message(thread_id, uid)

        # 列表包含该会话
        r = c.get("/api/v1/threads/", headers=h)
        assert r.json()["total"] == 1
        # 消息含 message_id + citations
        r = c.get(f"/api/v1/threads/{thread_id}/messages", headers=h)
        assert r.status_code == 200
        assert r.json()["messages"][0]["message_id"] == mid

        # 反馈：自己的消息 → 200
        r = c.post("/api/v1/feedback/", json={"message_id": mid, "rating": "helpful"}, headers=h)
        assert r.status_code == 200

        # 反馈：任填 UUID → 404
        r = c.post(
            "/api/v1/feedback/",
            json={"message_id": str(uuid.uuid4()), "rating": "helpful"},
            headers=h,
        )
        assert r.status_code == 404

        # 删除会话
        r = c.delete(f"/api/v1/threads/{thread_id}", headers=h)
        assert r.status_code == 200
        r = c.get(f"/api/v1/threads/{thread_id}/messages", headers=h)
        assert r.json()["messages"] == []


def test_feedback_ownership_isolation():
    with TestClient(app) as c:
        _, uidA = _register(c, "fb_a")
        tokB, _ = _register(c, "fb_b")

        thread_id = "th_" + uuid.uuid4().hex[:8]
        mid = _insert_message(thread_id, uidA)

        # B 对 A 的消息反馈 → 404（不暴露存在性）
        r = c.post(
            "/api/v1/feedback/",
            json={"message_id": mid, "rating": "helpful"},
            headers={"Authorization": f"Bearer {tokB}"},
        )
        assert r.status_code == 404
