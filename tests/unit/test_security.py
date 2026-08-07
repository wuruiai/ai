"""安全工具测试：路径白名单 + Origin 校验。"""

from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.core.security import resolve_client_ip, validate_file_path, validate_origin


def test_validate_file_path_within_root_ok():
    inside = Path(settings.DATA_ROOT) / "source" / "x.txt"
    p = validate_file_path(str(inside))
    assert p == inside.resolve()


def test_validate_file_path_outside_raises():
    with pytest.raises(HTTPException) as ei:
        validate_file_path(str(Path("C:/Windows/win.ini")))
    assert ei.value.status_code == 403


def test_validate_origin_no_origin_allowed():
    class Req:
        headers: ClassVar[dict] = {}

    validate_origin(Req())  # origin 为 None → 放行（本地工具/curl）


def test_validate_origin_rejects_unknown(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_ORIGIN", "http://ok.example.com")
    monkeypatch.setattr(settings, "EXTRA_ALLOWED_ORIGINS", "")

    class Req:
        headers: ClassVar[dict] = {"origin": "http://evil.example.com"}

    with pytest.raises(HTTPException) as ei:
        validate_origin(Req())
    assert ei.value.status_code == 403


def test_validate_origin_allows_whitelisted(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_ORIGIN", "http://ok.example.com")
    monkeypatch.setattr(settings, "EXTRA_ALLOWED_ORIGINS", "")

    class Req:
        headers: ClassVar[dict] = {"origin": "http://ok.example.com"}

    validate_origin(Req())  # 不抛


def _req_with_xff(xff: str | None, client: tuple | None = ("172.18.0.5", 54321)):
    from starlette.requests import Request

    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/auth/login",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": client,
        "server": ("127.0.0.1", 8001),
    }
    return Request(scope)


def test_resolve_client_ip_untrusted_ignores_xff(monkeypatch):
    """G10.17：peer 不在 TRUSTED_PROXIES 内 → 伪造的 XFF 被忽略，回退直连地址。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    assert resolve_client_ip(_req_with_xff("203.0.113.9")) == "172.18.0.5"


def test_resolve_client_ip_trusted_exact_match(monkeypatch):
    """G10.17：peer 精确匹配可信列表 → 采信 XFF 最左侧（真实客户端）。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "172.18.0.5")
    assert resolve_client_ip(_req_with_xff("203.0.113.9")) == "203.0.113.9"
    # 多级反代取最左侧，右侧为各级代理
    assert resolve_client_ip(_req_with_xff("203.0.113.9, 10.0.0.2")) == "203.0.113.9"


def test_resolve_client_ip_trusted_cidr(monkeypatch):
    """G10.17：TRUSTED_PROXIES 支持 CIDR（docker 网桥段覆盖反代容器 IP）。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "172.16.0.0/12")
    assert resolve_client_ip(_req_with_xff("198.51.100.7")) == "198.51.100.7"


def test_resolve_client_ip_no_client_no_xff(monkeypatch):
    """G10.17：无 socket 连接信息时返回空串（调用方再回退到 unknown）。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "testclient")
    assert resolve_client_ip(_req_with_xff(None, client=None)) == ""


def test_resolve_client_ip_no_xff_falls_back(monkeypatch):
    """G10.17：可信代理但无 XFF 头 → 回退直连地址。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "172.18.0.5")
    assert resolve_client_ip(_req_with_xff(None)) == "172.18.0.5"
