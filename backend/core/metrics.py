"""Prometheus /metrics 指标（G2.2 可观测性）

暴露标准 OpenMetrics 文本格式供 Prometheus 抓取：
- `http_requests_total`：请求计数（method / 归一化路径 / status）
- `http_request_duration_seconds`：请求耗时直方图

关键设计：
- 路径归一化：UUID / 长 hex / 长数字段折叠为 `{id}`，避免"每资源一个标签值"导致
  Prometheus 标签基数爆炸（如 /api/v1/documents/<uuid> 数百个不同标签）。
- 排除 /metrics 自身（避免抓取噪声自我累加）。
"""

from __future__ import annotations

import re
import time

from prometheus_client import Counter, Histogram, generate_latest
from prometheus_client.registry import REGISTRY

# 请求计数（status 细分为 2xx/4xx/5xx 便于 SLO 计算）
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ("method", "path", "status"),
)

# 耗时直方图：桶位贴近常见 web 延迟分布
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("method", "path"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# 抓取端点到自身不做统计（避免抓取动作污染业务指标）
_EXCLUDED_PATHS = {"/metrics"}

# 折叠 ID 段：先整段折叠 UUID（其内部短段如 4f53 若单独折叠会留下
# 随 UUID 变化的字面量，导致标签基数爆炸），再折叠其余 ≥8 位 hex 与 ≥4 位数字段。
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_ID_SEGMENT = re.compile(r"[0-9a-fA-F]{8,}|\d{4,}")


def normalize_path(path: str) -> str:
    """把路径里的 ID 段折叠成 {id}，控制标签基数。"""
    return _ID_SEGMENT.sub("{id}", _UUID.sub("{id}", path))


def instrument_request(method: str, path: str, status_code: int, duration_s: float) -> None:
    """记录一次已完成的 HTTP 请求（由中间件调用）。"""
    if path in _EXCLUDED_PATHS:
        return
    norm = normalize_path(path)
    HTTP_REQUESTS.labels(method, norm, str(status_code)).inc()
    HTTP_REQUEST_DURATION.labels(method, norm).observe(duration_s)


def render_metrics() -> bytes:
    """OpenMetrics 文本（Prometheus 抓取格式）。"""
    return generate_latest(REGISTRY)


def metrics_elapsed_ns() -> int:
    """时间戳助手：time.perf_counter_ns（避免中间件里重复 import）。"""
    return time.perf_counter_ns()
