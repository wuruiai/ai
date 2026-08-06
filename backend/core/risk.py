"""高风险问题检测

识别涉及防汛调度、工程安全、法规合规等高风险领域的问题，
在回答旁附加人工复核提示（方案文档 §1.3 边界 4：高风险保持人工边界）。

Reference: §10 安全边界
"""

from __future__ import annotations

import re

# 高风险关键词（命中任一即标记为高风险）
_HIGH_RISK_PATTERNS: list[str] = [
    "防汛调度",
    "泄洪",
    "开闸",
    "蓄洪",
    "分洪",
    "溃坝",
    "管涌",
    "渗流",
    "工程安全",
    "结构安全",
    "大坝安全",
    "地震",
    "滑坡",
    "泥石流",
    "应急预案",
    "抢险",
    "救灾",
    "水质标准",
    "饮用水安全",
    "污染物",
    "排污",
    "法规",
    "合规",
    "法律责任",
    "责令",
    "罚款",
    "调度令",
    "闸门操作",
    "泵站运行",
    "水位控制",
    "极限水位",
]

_COMPILED = [re.compile(p) for p in _HIGH_RISK_PATTERNS]

# 高风险提示文案
HIGH_RISK_WARNING = (
    "本问题涉及水利高风险领域（防汛调度/工程安全/法规合规）。"
    "以上回答仅供参考，不构成专业决策依据；"
    "实际调度、工程操作或合规判断必须由具备资质的人员复核确认。"
)


def is_high_risk(query: str) -> bool:
    """判断问题是否涉及高风险领域。"""
    if not query:
        return False
    for pat in _COMPILED:
        if pat.search(query):
            return True
    return False


def high_risk_warning() -> str:
    """获取高风险提示文案。"""
    return HIGH_RISK_WARNING
