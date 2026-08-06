"""成本刹车（配额/限流）

调用预算管理。

Reference: §4.8
"""

from collections import defaultdict
from datetime import date

from fastapi import HTTPException

from backend.config import settings


class BudgetManager:
    """预算管理器"""

    def __init__(self):
        self._daily_calls: dict[str, int] = defaultdict(int)
        self._current_date: date = date.today()

    def check_budget(self):
        """检查预算"""
        today = date.today()
        if today != self._current_date:
            self._daily_calls.clear()
            self._current_date = today

        total = sum(self._daily_calls.values())
        if total >= settings.DAILY_CALL_LIMIT:
            raise HTTPException(status_code=429, detail="Daily call limit exceeded")

    def record_call(self, model: str):
        """记录调用"""
        self._daily_calls[model] += 1


budget_manager = BudgetManager()
