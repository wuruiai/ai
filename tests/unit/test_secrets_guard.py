"""启动密钥 fail-fast（G10.5 M5）：ensure_secrets 非 local 环境强制校验。

构造独立 Settings 实例验证（不动模块单例 settings）：
    - local：豁免，缺密钥不报错（开发开箱即用）
    - 非 local（staging / production 等）：缺 TOKEN_SECRET / DASHSCOPE_API_KEY 抛 RuntimeError
    - 非 local 且密钥齐全：通过
"""

import pytest

from backend.config import Settings


def test_ensure_secrets_local_bypass():
    """local 环境豁免：缺密钥可启动（TOKEN_SECRET 空时进程级随机）。"""
    s = Settings(APP_ENV="local", TOKEN_SECRET="", DASHSCOPE_API_KEY="")
    s.ensure_secrets()  # 不抛


@pytest.mark.parametrize("app_env", ["production", "staging", "test"])
def test_ensure_secrets_nonlocal_blocks_missing(app_env):
    """任何非 local 环境：缺失任一关键密钥即拒绝启动。"""
    s = Settings(APP_ENV=app_env, TOKEN_SECRET="", DASHSCOPE_API_KEY="")
    with pytest.raises(RuntimeError, match="TOKEN_SECRET"):
        s.ensure_secrets()

    s = Settings(APP_ENV=app_env, TOKEN_SECRET="x", DASHSCOPE_API_KEY="")  # noqa: S106
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        s.ensure_secrets()


def test_ensure_secrets_nonlocal_ok_when_set():
    """非 local 且密钥齐全：通过。"""
    s = Settings(
        APP_ENV="production",
        TOKEN_SECRET="x",  # noqa: S106
        DASHSCOPE_API_KEY="y",
    )
    s.ensure_secrets()  # 不抛
