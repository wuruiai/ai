"""路径白名单 + origin 校验 + 客户端 IP 解析

安全工具。

"""

import ipaddress
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


def _is_trusted_proxy(peer: str) -> bool:
    """peer（直连地址）是否在 TRUSTED_PROXIES 配置内（支持精确匹配与 CIDR）。"""
    if not settings.TRUSTED_PROXIES.strip():
        return False
    for entry in settings.TRUSTED_PROXIES.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "/" in entry:
            try:
                if ipaddress.ip_address(peer) in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue  # 非法 CIDR 配置忽略，不因配置错误误伤
        elif entry == peer:
            return True
    return False


def resolve_client_ip(request: Request) -> str:
    """真实客户端 IP，仅信任来自可信代理的 X-Forwarded-For。

    威胁（S2）：无条件采信 XFF 最左侧，攻击者可伪造该头逐条旋转"来源 IP"，
    规避登录/注册的 IP 维度限流（也让审计 IP 失真）。只有当直连 peer
    （request.client.host）落在 TRUSTED_PROXIES 内时才采信 XFF（反代 nginx 场景，
    nginx 会按真实来源重写该头）；否则一律回退直连地址——直连场景下 XFF 完全
    不可信（客户端可自编该头，且反代未配置信任时更不该采信）。main.py access
    日志与 auth 限流/审计统一走这里，保证口径一致。
    """
    peer = request.client.host if request.client else ""
    if peer and _is_trusted_proxy(peer):
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
    return peer
