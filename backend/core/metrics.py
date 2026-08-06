"""Prometheus /metrics 指标（G2.2 可观测性）

暴露标准 OpenMetrics 文本格式供 Prometheus 抓取：
- `http_requests_total`：请求计数（method / 归一化路径 / status）
- `http_request_duration_seconds`：请求耗时直方图
- `llm_calls_total` / `llm_tokens_total` / `llm_call_duration_seconds` /
  `llm_cost_cny_total`：LLM 调用聚合指标（G10.9 M10，按 model 分维度）

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

# LLM 调用指标（G10.9 M10）：次数 / token / 耗时 / 成本，按 model 分维度。
# 由 UsageCollector 逐调用打点（on_llm_start 记开始时间，on_llm_end 落点），
# 与 llm_usage 表（明细/审计）互补——这里是实时聚合，供 Grafana 看板。
LLM_CALLS = Counter(
    "llm_calls_total",
    "LLM invocations (successful usage-bearing calls)",
    ("model",),
)
LLM_TOKENS = Counter(
    "llm_tokens_total",
    "LLM prompt/completion tokens",
    ("model", "kind"),
)
# 桶位贴近 LLM 调用延迟分布：秒级到分钟级（长文档生成可达 60s+）
LLM_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM call latency in seconds",
    ("model",),
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)
LLM_COST = Counter(
    "llm_cost_cny_total",
    "Estimated LLM cost in CNY",
    ("model",),
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


def record_llm_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_cny: float,
    duration_s: float,
) -> None:
    """记录一次 LLM 调用（由 UsageCollector.on_llm_end 逐调用调用）。

    次数 + 耗时对所有调用打点；token / 成本仅在有 usage 信息时非 0。
    cost_cny 由调用方按 settings 单价折算（此处不 import usage，避免循环依赖）。
    """
    model = model or "unknown"
    LLM_CALLS.labels(model).inc()
    LLM_DURATION.labels(model).observe(duration_s)
    if input_tokens > 0:
        LLM_TOKENS.labels(model, "input").inc(input_tokens)
    if output_tokens > 0:
        LLM_TOKENS.labels(model, "output").inc(output_tokens)
    if cost_cny > 0:
        LLM_COST.labels(model).inc(cost_cny)


def render_metrics() -> bytes:
    """OpenMetrics 文本（Prometheus 抓取格式）。"""
    return generate_latest(REGISTRY)


def metrics_elapsed_ns() -> int:
    """时间戳助手：time.perf_counter_ns（避免中间件里重复 import）。"""
    return time.perf_counter_ns()
