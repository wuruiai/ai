"""本地 JSON 结构化日志

日志配置和工具。

Reference: §3.2
"""

import logging
from datetime import datetime
from pathlib import Path

from backend.config import settings


def get_logger(name: str) -> logging.Logger:
    """获取配置好的日志器（通常传 __name__）"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # 文件处理器
    log_dir = Path(settings.LOG_PATH)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    return logger
