"""可观测性测试：健康/就绪探针 + Prometheus 指标 + 路径归一化 + LLM 指标 + 日志统一。

覆盖 G2.1-G2.3 与 G10.9（M10 LLM 指标、M11 结构化 access 日志 / uvicorn 日志并入 JSON）。
"""

import logging

from fastapi.testclient import TestClient

from backend.core.metrics import normalize_path, record_llm_call, render_metrics
from backend.core.usage import UsageCollector
from backend.db.migrations import SCHEMA_VERSION
from backend.main import app


def test_health_liveness():
    """存活探针：进程在即 200，且不回显依赖细节（供高频探活）。"""
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"]
        assert body["schema_version"] == SCHEMA_VERSION


def test_health_ready_checks_deps():
    """就绪探针：SQLite + Chroma 可达 → 200 ready；缺失 → 503 not_ready。"""
    with TestClient(app) as c:
        r = c.get("/health/ready")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] is True
        assert body["checks"]["chroma"] is True


def test_metrics_exposition():
    """/metrics 输出 Prometheus 文本，且记录了本进程请求。"""
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        text = r.text
        assert "http_requests_total" in text
        assert "http_request_duration_seconds" in text
        # 已发生请求被统计，且路径做了归一化
        assert 'path="/health"' in text


def test_metrics_excludes_self():
    """/metrics 自身不计入请求计数（避免抓取噪声自我累加）。"""
    with TestClient(app) as c:
        c.get("/metrics")
        c.get("/metrics")
        r = c.get("/metrics")
        text = r.text
        # 黑盒断言：导出文本里不应存在 path="/metrics" 的样本
        assert 'path="/metrics"' not in text
        assert "/metrics" not in [ln.split()[0] for ln in text.splitlines() if "path=" in ln]


def test_normalize_path_collapses_ids():
    """不同 UUID 折叠成同一标签，防止 Prometheus 标签基数爆炸。"""
    assert normalize_path("/api/v1/documents/af701cca-7898-4f53-a23c-3f5fdb8f3d91") == (
        "/api/v1/documents/{id}"
    )
    assert normalize_path("/api/v1/documents/11223344-5566-7788-99aa-bbccddeeff00") == (
        "/api/v1/documents/{id}"
    )
    # 非 ID 路径不受影响
    assert normalize_path("/api/v1/auth/login") == "/api/v1/auth/login"
    assert normalize_path("/health/ready") == "/health/ready"


def test_request_id_echoed():
    """请求中间件：客户端传入 X-Request-ID 时原样回传，便于链路追踪。"""
    with TestClient(app) as c:
        r = c.get("/health", headers={"X-Request-ID": "trace-001"})
        assert r.headers.get("X-Request-ID") == "trace-001"


# ---------------------------------------------------------------------------
# G10.9 M10：LLM 指标（调用次数 / token / 延迟 / 成本）
# ---------------------------------------------------------------------------


def test_record_llm_call_emits_metrics():
    """record_llm_call 打点后 /metrics 含对应 LLM 样本（按 model 分维度）。

    用唯一 model 名隔离，避免跨测试累加到全局 Counter 造成断言污染。
    """
    model = "test-llm-metrics-model"
    record_llm_call(model, 10, 20, 0.03, 1.5)
    text = render_metrics().decode()
    # 标签按字典序输出（prometheus_client）：kind < model
    assert f'llm_calls_total{{model="{model}"}} 1.0' in text
    assert f'llm_tokens_total{{kind="input",model="{model}"}} 10.0' in text
    assert f'llm_tokens_total{{kind="output",model="{model}"}} 20.0' in text
    assert f'llm_cost_cny_total{{model="{model}"}} 0.03' in text
    assert f'llm_call_duration_seconds_count{{model="{model}"}} 1.0' in text


def test_usage_collector_records_llm_metrics_per_call():
    """UsageCollector.on_llm_end 逐调用打点 LLM 指标（集成：延迟+token+成本）。"""

    class _Resp:
        def __init__(self, model: str):
            self.llm_output = {
                "token_usage": {"prompt_tokens": 8, "completion_tokens": 12},
                "model_name": model,
            }
            self.generations = []

    model = "test-usage-collector-model"
    collector = UsageCollector()
    collector.on_llm_start({}, ["问题"])
    collector.on_llm_end(_Resp(model))

    text = render_metrics().decode()
    assert f'llm_calls_total{{model="{model}"}} 1.0' in text
    assert f'llm_tokens_total{{kind="input",model="{model}"}} 8.0' in text
    assert f'llm_tokens_total{{kind="output",model="{model}"}} 12.0' in text
    # 记账同步累加（与指标不冲突）
    assert collector.input_tokens == 8
    assert collector.output_tokens == 12
    assert collector.model == model


def test_usage_collector_records_latency_even_without_usage():
    """无 usage 信息（异常/降级路径）仍打点调用次数 + 延迟，不累加记账。"""

    class _Resp:
        def __init__(self):
            self.llm_output = {"model_name": "test-usage-no-token-model"}
            self.generations = []

    model = "test-usage-no-token-model"
    collector = UsageCollector()
    collector.on_llm_start({}, ["问题"])
    collector.on_llm_end(_Resp())

    text = render_metrics().decode()
    assert f'llm_calls_total{{model="{model}"}} 1.0' in text
    # 无 token：记账保持 0
    assert collector.has_usage is False


# ---------------------------------------------------------------------------
# G10.9 M11：日志统一——结构化 access 日志 + uvicorn 日志并入根 JSON
# ---------------------------------------------------------------------------


def test_access_log_is_structured_json(caplog):
    """中间件输出结构化 access 日志：method/path/status/duration_ms/client 字段。"""
    with caplog.at_level(logging.INFO):
        with TestClient(app) as c:
            c.get("/health", headers={"X-Forwarded-For": "203.0.113.7"})
    access = [r for r in caplog.records if r.name == "app.access"]
    assert access, "access log 未输出"
    last = access[-1]
    assert last.message == "http request"
    assert last.method == "GET"
    assert last.path == "/health"
    assert last.status == 200
    assert last.duration_ms is not None
    # 经 X-Forwarded-For 解析到真实客户端 IP（与 auth 限流口径一致）
    assert last.client == "203.0.113.7"


def test_unify_uvicorn_logging_inherits_root_json():
    """uvicorn 日志并入根 JSON：关闭默认 access、error 向根 propagate。"""
    from backend.core.logger import unify_uvicorn_logging

    unify_uvicorn_logging()
    assert logging.getLogger("uvicorn.access").disabled is True
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        assert lg.propagate is True
        assert lg.handlers == []  # 不再用 uvicorn 默认文本 handler
