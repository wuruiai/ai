"""DashScope Rerank 精排测试（mock HTTP，G10.25 测试缺口补齐）。

rerank() 是对 DashScope 的 HTTP 封装：构造请求 → 校验状态码 → 解析
`output.results` 为 `[{"index", "score"}]`。用假客户端断言请求体与解析逻辑，
不触网；非 2xx 抛 httpx.HTTPStatusError（调用方 rerank_node 据此降级）。
"""

import httpx
import pytest

import backend.rag.reranker as reranker_mod
from backend.config import settings


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "fake status error", request=httpx.Request("POST", "http://fake"), response=self
            )

    def json(self) -> dict:
        return self._json


class _FakeClient:
    """复刻 httpx.AsyncClient 的 async 上下文管理器接口；记录最后一次请求参数。"""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_kwargs: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
        self.last_kwargs = {"url": url, "headers": headers, "json": json}
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse) -> _FakeClient:
    fake = _FakeClient(response)
    monkeypatch.setattr(reranker_mod, "create_http_client", lambda: fake)
    return fake


async def test_rerank_builds_request_and_parses_results(monkeypatch):
    """请求体含 model/query/documents/top_n；输出解析为 index+score 列表。"""
    resp_json = {
        "output": {
            "results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.3},
            ]
        }
    }
    fake = _patch_client(monkeypatch, _FakeResponse(200, resp_json))

    docs = ["水库调度原则", "汛期防洪", "大坝安全鉴定"]
    result = await reranker_mod.rerank("水库调度", docs, top_k=2)

    # 请求体与配置一致
    sent = fake.last_kwargs
    assert sent["url"] == settings.DASHSCOPE_RERANK_URL
    assert sent["headers"]["Authorization"] == f"Bearer {settings.DASHSCOPE_API_KEY}"
    assert sent["json"]["model"] == settings.RERANK_MODEL
    assert sent["json"]["input"] == {"query": "水库调度", "documents": docs}
    assert sent["json"]["parameters"]["top_n"] == 2  # 显式 top_k 覆盖默认

    # 解析：按 DashScope 返回序输出 index+score（保持服务端顺序，不做本地排序）
    assert result == [
        {"index": 2, "score": 0.9},
        {"index": 0, "score": 0.5},
        {"index": 1, "score": 0.3},
    ]


async def test_rerank_defaults_top_k_to_settings(monkeypatch):
    """未传 top_k 时使用 settings.RERANK_TOP_K。"""
    fake = _patch_client(monkeypatch, _FakeResponse(200, {"output": {"results": []}}))
    await reranker_mod.rerank("问题", ["文档"])

    assert fake.last_kwargs["json"]["parameters"]["top_n"] == settings.RERANK_TOP_K


async def test_rerank_raises_on_non_2xx(monkeypatch):
    """403（账号未开通权限）→ raise_for_status 抛 httpx.HTTPStatusError。"""
    _patch_client(monkeypatch, _FakeResponse(403, {}))
    with pytest.raises(httpx.HTTPStatusError):
        await reranker_mod.rerank("问题", ["文档"])


async def test_rerank_empty_output_returns_empty_list(monkeypatch):
    """output.results 缺失/为空 → 返回空列表，不抛错（调用方按空证据降级）。"""
    _patch_client(monkeypatch, _FakeResponse(200, {"output": {}}))
    assert await reranker_mod.rerank("问题", ["文档"]) == []

    _patch_client(monkeypatch, _FakeResponse(200, {"output": {"results": []}}))
    assert await reranker_mod.rerank("问题", ["文档"]) == []
