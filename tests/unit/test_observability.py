"""可观测性测试：健康/就绪探针 + Prometheus 指标 + 路径归一化（G2.1-G2.3）。"""

from fastapi.testclient import TestClient

from backend.core.metrics import normalize_path
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
