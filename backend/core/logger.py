"""JSON 结构化日志 + request_id 链路追踪

设计要点（G2.1 可观测性）：
- 根 logger 统一配置一次（幂等）；所有模块通过 `get_logger(__name__)` 拿到同一棵 logger 树，
  不再每次调用都新挂 handler（旧实现每模块 2 个 handler，日志文件句柄随模块数膨胀）。
- 输出单行 JSON（Loki / ELK / Promtail 直接可采集），中文 ensure_ascii=False 保持可读。
- `request_id` 用 ContextVar 注入：HTTP 中间件把 X-Request-ID 写入上下文，本请求内所有日志自动带
  `request_id` 字段，便于跨模块/跨日志聚合排障。
- `get_logger` 首次调用时自动触发 `setup_logging()`，任何入口（uvicorn / 运维脚本 / 测试）都不会拿到
  无 handler 的 logger。uvicorn 自身日志由其独立 logger 输出，保持默认文本，不影响本应用 JSON 化。

"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from backend.config import settings

# 当前请求的 request_id（空 = 无请求上下文，如启动日志/后台任务）
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    """由 HTTP 中间件写入当前请求的 request_id。"""
    request_id_ctx.set(request_id)


def get_request_id() -> str:
    return request_id_ctx.get()


class JsonFormatter(logging.Formatter):
    """单行 JSON 格式化器：{ts, level, logger, message, request_id?, exc_info?}"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        req_id = request_id_ctx.get()
        if req_id:
            entry["request_id"] = req_id
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_configured = False


def setup_logging() -> None:
    """配置根 logger（幂等）：控制台 + 文件均为 JSON 单行。"""
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(JsonFormatter())
    root.handlers = [console]

    # 文件日志：按天落盘；目录不可写时降级为仅控制台（不拖垮进程）
    try:
        log_dir = Path(settings.LOG_PATH)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log",
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        root.handlers.append(file_handler)
    except OSError:
        # 文件日志不可用（只读容器等）只影响落盘，不影响控制台日志
        pass


def get_logger(name: str) -> logging.Logger:
    """获取配置好的日志器（通常传 __name__）。首次调用自动完成全局配置。"""
    setup_logging()
    return logging.getLogger(name)
