"""thread_id 管理 + 滑动窗口摘要 + MemorySaver

对话记忆管理。

Reference: §9.2 / §9.4, EduAgent memory
"""

from dataclasses import dataclass, field

from langgraph.checkpoint.memory import MemorySaver

from backend.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ConversationMemory:
    """对话记忆"""

    thread_id: str
    messages: list[dict] = field(default_factory=list)
    summary: str | None = None

    def add_message(self, role: str, content: str):
        """添加消息"""
        self.messages.append({"role": role, "content": content})

    def get_messages(self, max_tokens: int = 4000) -> list[dict]:
        """获取消息（滑动窗口）"""
        # TODO: 实现滑动窗口截断
        return self.messages


class MemoryManager:
    """记忆管理器"""

    def __init__(self):
        self._memories: dict[str, ConversationMemory] = {}

    def get_or_create(self, thread_id: str) -> ConversationMemory:
        """获取或创建记忆"""
        if thread_id not in self._memories:
            self._memories[thread_id] = ConversationMemory(thread_id=thread_id)
        return self._memories[thread_id]


# MemorySaver 实例缓存
_memory_savers: dict[str, MemorySaver] = {}


def get_memory_saver(agent_type: str) -> MemorySaver:
    """
    获取 MemorySaver 实例。

    每个 Agent 类型使用独立的 MemorySaver，避免检查点串台。

    Args:
        agent_type: Agent 类型标识（如 "knowledge_qa", "water_expert"）

    Returns:
        MemorySaver 实例
    """
    if agent_type not in _memory_savers:
        _memory_savers[agent_type] = MemorySaver()
        logger.info("MemorySaver created for agent: %s", agent_type)
    return _memory_savers[agent_type]


memory_manager = MemoryManager()
