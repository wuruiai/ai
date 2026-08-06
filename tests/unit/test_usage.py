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


def _chat_chunk(usage_metadata, model=""):
    """模拟回调 chunk：ChatGenerationChunk 包装，usage 在其 .message（AIMessageChunk）上。"""
    return SimpleNamespace(message=SimpleNamespace(usage_metadata=usage_metadata, model=model))


def test_collector_captures_usage_from_stream_chunk():
    """langchain-openai 1.x 流式路径：on_llm_end 聚合结果无 usage，
    但带 stream_usage=True 后 chunk 携带 usage_metadata → 从 chunk 捕获。"""
    c = UsageCollector()
    # 无 usage 的普通 chunk 不产生记录
    c.on_llm_new_token("水", chunk=_chat_chunk(None))
    # 最后一个 chunk 携带本调用总用量（OpenAI 兼容流惯例）
    c.on_llm_new_token(
        "好",
        chunk=_chat_chunk(
            {"input_tokens": 14, "output_tokens": 1, "total_tokens": 15}, "qwen-plus"
        ),
    )
    # 流式聚合结果无 usage → 走 chunk 回退
    c.on_llm_end(SimpleNamespace(llm_output=None, generations=[]))
    assert (c.input_tokens, c.output_tokens) == (14, 1)
    assert c.has_usage


def test_collector_stream_chunk_resets_after_end():
    """每轮 on_llm_end 后清空暂存，避免上一轮 chunk 用量泄漏到下一轮。"""
    c = UsageCollector()
    c.on_llm_new_token("好", chunk=_chat_chunk({"input_tokens": 7, "output_tokens": 2}))
    c.on_llm_end(SimpleNamespace(llm_output=None, generations=[]))
    # 第二轮无 chunk 用量、无聚合 usage → 静默跳过，不重复累加
    c.on_llm_end(SimpleNamespace(llm_output=None, generations=[]))
    assert (c.input_tokens, c.output_tokens) == (7, 2)


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
