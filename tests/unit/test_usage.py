"""LLM 用量记账测试（G3.1）：collector 提取 / flush 落库 / 成本换算 / 管理端聚合。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.core.usage import UsageCollector, usage_cost_cny
from backend.db.connection import close_db, get_connection
from backend.db.migrations import migrate
from backend.main import app


def _llm_result(prompt: int, completion: int, model: str = "qwen-plus"):
    return SimpleNamespace(
        llm_output={
            "token_usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
            },
            "model_name": model,
        },
        generations=[],
    )


def test_usage_cost_cny():
    # 输入 1M、输出 0.5M token → 0.8 + 2.0 × 0.5 = 1.8 元
    assert usage_cost_cny(1_000_000, 500_000) == pytest.approx(1.8)


def test_collector_accumulates_from_llm_output():
    c = UsageCollector()
    c.on_llm_end(_llm_result(120, 30, "qwen-plus"))
    c.on_llm_end(_llm_result(80, 20, "qwen-plus"))
    assert c.input_tokens == 200
    assert c.output_tokens == 50
    assert c.model == "qwen-plus"
    assert c.has_usage


def test_collector_skips_when_no_usage():
    c = UsageCollector()
    c.on_llm_end(SimpleNamespace(llm_output=None, generations=[]))
    assert not c.has_usage
    assert (c.input_tokens, c.output_tokens) == (0, 0)


def test_collector_fallback_generation_info():
    gen = SimpleNamespace(
        generation_info={"token_usage": {"prompt_tokens": 5, "completion_tokens": 3}}
    )
    c = UsageCollector()
    c.on_llm_end(SimpleNamespace(llm_output={}, generations=[[gen]]))
    assert (c.input_tokens, c.output_tokens) == (5, 3)


def test_collector_fallback_usage_metadata():
    gen = SimpleNamespace(
        generation_info={"usage_metadata": {"input_tokens": 7, "output_tokens": 4}}
    )
    c = UsageCollector()
    c.on_llm_end(SimpleNamespace(llm_output={}, generations=[[gen]]))
    assert (c.input_tokens, c.output_tokens) == (7, 4)


async def test_flush_persists_row():
    db = await get_connection()
    await migrate(db)
    await close_db(db)

    c = UsageCollector()
    c.on_llm_end(_llm_result(100, 50, "qwen-plus"))
    await c.flush("local_user", agent_type="knowledge_qa")

    db = await get_connection()
    try:
        async with db.execute(
            "SELECT user_id, model, input_tokens, output_tokens, cost_cny FROM llm_usage"
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "local_user"
        assert rows[0][2] == 100
        assert rows[0][4] == pytest.approx(usage_cost_cny(100, 50))
    finally:
        await close_db(db)


async def test_flush_skips_when_no_usage():
    db = await get_connection()
    await migrate(db)
    await close_db(db)

    await UsageCollector().flush("local_user")

    db = await get_connection()
    try:
        async with db.execute("SELECT COUNT(*) FROM llm_usage") as cur:
            assert (await cur.fetchone())[0] == 0
    finally:
        await close_db(db)


def test_admin_usage_endpoint():
    """注册首个用户即 admin → GET /admin/usage 返回空聚合 + 14 天占位。"""
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/auth/register",
            json={"username": "root_usage", "password": "pass123456"},
        )
        tok = r.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}

        r = c.get("/api/v1/admin/usage", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["total_calls"] == 0
        assert len(body["days"]) == 14
        assert body["days"][0]["calls"] == 0
