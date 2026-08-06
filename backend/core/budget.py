"""成本刹车（配额/限流）

调用预算管理。

Reference: §4.8

单进程用内存后端；设 `REDIS_URL` 后工厂返回 Redis 后端（跨进程共享计数）。
public 名字 `BudgetManager` 保留（即内存实现），模块单例 `budget_manager` 走工厂。
"""

from __future__ import annotations

from backend.core.backends import InMemoryBudgetBackend, get_budget_backend

# 兼容旧引用：BudgetManager 即内存预算后端
BudgetManager = InMemoryBudgetBackend

# 模块单例：REDIS_URL 非空时自动换 Redis 后端
budget_manager = get_budget_backend()
