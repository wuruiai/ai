"""pytest 根配置：单测统一使用临时数据目录，绝不触碰真实 data/。

注意：这些环境变量必须在任何 backend.* 模块被 import 之前设置，
pydantic-settings 会在 settings 单例首次读取时固化配置。
"""

import os
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="waterrag_unit_")

os.environ.setdefault("SQLITE_PATH", os.path.join(_TMP, "water.db"))
# 强制禁用连接池：_fresh_db 每个测试删除 DB 文件重建，
# 若复用指向旧文件的连接会读到已删除的库（G4.2）
os.environ["DB_POOL_SIZE"] = "0"
os.environ.setdefault("CHROMA_PATH", os.path.join(_TMP, "chroma"))
os.environ.setdefault("DATA_ROOT", _TMP)
os.environ.setdefault("SOURCE_PATH", os.path.join(_TMP, "source"))
# 日志落到临时目录，避免测试污染真实 ./data/logs
os.environ.setdefault("LOG_PATH", os.path.join(_TMP, "logs"))
# 假 key：单测不调云端；仅用于绕过“空 key”相关分支
os.environ.setdefault("DASHSCOPE_API_KEY", "unit-test-fake-key")
os.environ.setdefault("DAILY_CALL_LIMIT", "1000")


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个测试前清空临时 SQLite + 内存状态，保证测试顺序无关（每个测试内“首个注册=admin”）。"""
    from backend.config import settings
    from backend.core.rate_limit import login_throttle

    for p in (
        Path(settings.SQLITE_PATH),
        Path(settings.SQLITE_PATH + "-wal"),
        Path(settings.SQLITE_PATH + "-shm"),
    ):
        p.unlink(missing_ok=True)
    # 登录防爆破是进程级内存状态，逐测试清空，避免跨用例累计
    login_throttle._failures.clear()
    login_throttle._lockout_until.clear()
    yield
