"""定时备份守护（G5.2）

跨平台定时备份入口，受 `BACKUP_ENABLED` 控制：

  - `--once` 模式：执行一次后退出。适合 Windows 计划任务 / 系统 cron 每日触发
    （计划任务只管按时调用，具体每天备份一次还是多次由调度器决定）。
  - 循环模式（默认）：常驻进程，每隔 `--interval-hours` 小时备份一次。
    适合 docker 内作为 sidecar 常驻，或进程守护（systemd / NSSM）管理。

BACKUP_ENABLED=false 时：任何模式下都跳过备份（保留调度但什么都不做），
用于临时关闭备份又不撤销计划任务。

Usage:
    python -m scripts.backup_cron --once
    python -m scripts.backup_cron --interval-hours 24

Windows 计划任务示例（每日 02:00 备份一次）：
    schtasks /Create /SC DAILY /ST 02:00 /TN "water-rag-backup" ^
      /TR "cd /d <项目根目录> && .venv\\Scripts\\python -m scripts.backup_cron --once"

Docker cron 示例（每 3 小时备份一次）：
    docker run -d --name water-backup-cron -v water-data:/app/data ^
      <镜像> python -m scripts.backup_cron --interval-hours 3
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# 脚本独立运行：未 pip install 时把项目根加入 sys.path，保证 backend 包可直接导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings
from scripts.backup_data import run_backup

logger = logging.getLogger("backup_cron")


def _run_once() -> int:
    """执行一次备份，返回进程退出码。"""
    if not settings.BACKUP_ENABLED:
        print("BACKUP_ENABLED=false，跳过本次备份")
        return 0
    logger.info("开始备份")
    rc = run_backup()
    logger.info("备份结果 rc=%s", rc)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="定时备份守护")
    parser.add_argument("--once", action="store_true", help="只备份一次后退出（计划任务模式）")
    parser.add_argument("--interval-hours", type=int, default=24, help="循环模式备份间隔（小时）")
    args = parser.parse_args()

    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.once:
        return _run_once()

    if not settings.BACKUP_ENABLED:
        print("BACKUP_ENABLED=false，循环模式直接退出")
        return 0

    interval_s = max(1, args.interval_hours) * 3600
    logger.info("循环模式启动：每 %s 小时备份一次", args.interval_hours)
    try:
        while True:
            _run_once()
            time.sleep(interval_s)
    except KeyboardInterrupt:
        logger.info("收到中断，退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
