"""路径白名单 + origin 校验

安全工具。

"""

from pathlib import Path

from fastapi import HTTPException, Request

from backend.config import settings


def validate_file_path(file_path: str) -> Path:
    """验证文件路径在 DATA_ROOT 内（路径白名单）。

    用 commonpath 判断包含关系，避免 startswith 前缀匹配漏洞
    （如 /data/evil 误匹配 /data）。
    """
    path = Path(file_path).resolve()
    data_root = Path(settings.DATA_ROOT).resolve()

    try:
        is_within = path == data_root or path.is_relative_to(data_root)
    except AttributeError:
        # Python <3.9 兜底（本项目 3.11，通常不触发）
        is_within = str(path).startswith(str(data_root) + chr(92)) or str(path).startswith(
            str(data_root) + "/"
        )

    if not is_within:
        raise HTTPException(status_code=403, detail="Access denied")
    return path


def validate_origin(request: Request):
    """验证 Origin（白名单见 settings.allowed_origins）"""
    origin = request.headers.get("origin")
    if origin and origin not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="Invalid origin")
