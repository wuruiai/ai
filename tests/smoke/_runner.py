"""Backend runner: launch uvicorn in a subprocess and wait for /health.

Used by scripts/smoke_test.py to be self-contained — no manual
"start backend then run test" required.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_health(base_url: str, deadline_s: float) -> None:
    deadline = time.monotonic() + deadline_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            # base_url 由本文件用固定 host/port 构造（http://127.0.0.1），无用户输入
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as r:  # noqa: S310
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.3)
    raise RuntimeError(
        f"backend did not become healthy within {deadline_s:.0f}s; last_err={last_err}"
    )


@contextmanager
def backend_running(
    project_root: Path,
    host: str = "127.0.0.1",
    port: int = 8001,
    startup_timeout_s: float = 30.0,
):
    """Start uvicorn in a subprocess, yield base_url, terminate on exit.

    用法:
        with backend_running(Path('.'), port=8123) as base:
            ... # base = 'http://127.0.0.1:8123'
    """
    base = f"http://{host}:{port}"
    if _port_open(host, port):
        raise RuntimeError(f"port {port} already in use; refuse to start backend")

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    # smoke test 端口注入 Origin 白名单；生产不会设置该 env
    env.setdefault(
        "EXTRA_ALLOWED_ORIGINS",
        f"http://{host}:{port}",
    )
    # 隔离数据：smoke test 用临时 SQLite/Chroma，不污染真实数据
    # （企业级 RBAC 后首个注册用户为 admin，隔离库保证测试稳定可重复）
    import tempfile

    tmp = tempfile.mkdtemp(prefix="waterrag_smoke_")
    env.setdefault("SQLITE_PATH", str(Path(tmp) / "water.db"))
    env.setdefault("CHROMA_PATH", str(Path(tmp) / "chroma"))
    env.setdefault("DATA_ROOT", tmp)
    env.setdefault("SOURCE_PATH", str(Path(tmp) / "source"))
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    # cmd 由 sys.executable + 固定字面量组成，无外部/用户输入注入
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(project_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_health(base, startup_timeout_s)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
