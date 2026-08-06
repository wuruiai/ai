"""安全工具测试：路径白名单 + Origin 校验。"""

from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.core.security import validate_file_path, validate_origin


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
