"""每日调用限额测试（每用户计数）。"""

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.core.budget import BudgetManager


def test_budget_over_limit_raises_429(monkeypatch):
    bm = BudgetManager()
    monkeypatch.setattr(bm, "_daily_calls", {"u1": {"qwen-plus": 5}})
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 5)
    with pytest.raises(HTTPException) as ei:
        bm.check_budget("u1")
    assert ei.value.status_code == 429


def test_budget_under_limit_passes_and_records(monkeypatch):
    bm = BudgetManager()
    monkeypatch.setattr(bm, "_daily_calls", {"u1": {"qwen-plus": 1}})
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 5)
    bm.check_budget("u1")  # 不抛
    bm.record_call("u1", "qwen-plus")
    assert bm._daily_calls["u1"]["qwen-plus"] == 2


def test_budget_per_user_isolation(monkeypatch):
    """预算按用户隔离：A 超限 429 不影响 B 正常调用。"""
    bm = BudgetManager()
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 2)
    bm.record_call("alice", "qwen-plus")
    bm.record_call("alice", "qwen-max")
    with pytest.raises(HTTPException):
        bm.check_budget("alice")  # 2 >= 2 → 429
    bm.check_budget("bob")  # 未超限不抛
    bm.record_call("bob", "qwen-plus")
    assert bm._daily_calls["bob"]["qwen-plus"] == 1


def test_budget_date_rollover_clears(monkeypatch):
    bm = BudgetManager()
    bm._daily_calls["u1"]["qwen-plus"] = 3
    # 强制换天：_current_date 设成昨天
    bm._current_date = date.today() - timedelta(days=1)
    monkeypatch.setattr(settings, "DAILY_CALL_LIMIT", 100)
    bm.check_budget("u1")  # 触发换天 → 清空计数
    assert bm._daily_calls == {}
