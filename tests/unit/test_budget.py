"""每日调用限额测试。"""

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.core.budget import BudgetManager


def test_budget_over_limit_raises_429(monkeypatch):
    bm = BudgetManager()
    monkeypatch.setattr(bm, "_daily_calls", {"qwen-plus": 5})
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 5)
    with pytest.raises(HTTPException) as ei:
        bm.check_budget()
    assert ei.value.status_code == 429


def test_budget_under_limit_passes_and_records(monkeypatch):
    bm = BudgetManager()
    monkeypatch.setattr(bm, "_daily_calls", {"qwen-plus": 1})
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 5)
    bm.check_budget()  # 不抛
    bm.record_call("qwen-plus")
    assert bm._daily_calls["qwen-plus"] == 2


def test_budget_date_rollover_clears(monkeypatch):
    bm = BudgetManager()
    bm._daily_calls["x"] = 3
    # 强制换天：_current_date 设成昨天
    bm._current_date = date.today() - timedelta(days=1)
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 100)
    bm.check_budget()  # 触发换天 → 清空计数
    assert bm._daily_calls == {}
