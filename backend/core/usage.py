"""LLM token / 成本记账（G3.1）

TokenStreamHandler 只负责逐 token 推送；UsageCollector 作为同一回调链上的
兄弟 handler，在 `on_llm_end` 里捕获 usage（prompt/completion tokens），
由调用方在请求结束时 `flush()` 到 llm_usage 表，供 `/admin/usage` 聚合展示。

落地位置：chat.py 创建 `[stream_handler, usage_collector]` 一并塞入
`llm_callbacks`，经 orchestrator → state → 各 LLM 节点回调链收集。
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

from backend.config import settings
from backend.core.logger import get_logger
from backend.db.connection import close_db, get_connection

logger = get_logger(__name__)


def usage_cost_cny(input_tokens: int, output_tokens: int) -> float:
    """按 LLM_PRICE_*_PER_M（元/百万 token）折算人民币成本。"""
    return (
        input_tokens / 1_000_000 * settings.LLM_PRICE_INPUT_PER_M
        + output_tokens / 1_000_000 * settings.LLM_PRICE_OUTPUT_PER_M
    )


class UsageCollector(BaseCallbackHandler):
    """收集一次请求内全部 LLM 调用的 token 用量。

    - 与 TokenStreamHandler 一起放入 llm_callbacks
    - `on_llm_end` 从 LLMResult.llm_output['token_usage'] 提取（langchain-openai 标准格式）
    - 多轮 LLM 调用累加 input/output tokens
    - `flush()` 一次性写入 llm_usage 表；无用量（异常/降级）时静默跳过
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.model = ""

    @property
    def has_usage(self) -> bool:
        return self.input_tokens > 0 or self.output_tokens > 0

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        # 只关心 on_llm_end 的 usage；逐 token 回调由 TokenStreamHandler 消费
        pass

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        prompt, completion, model = _extract_usage(response)
        if prompt is None:
            return  # 无 usage 信息（异常/降级/测试 stub），静默跳过
        self.input_tokens += prompt
        self.output_tokens += completion
        if model:
            self.model = model

    async def flush(self, user_id: str, agent_type: str = "knowledge_qa") -> None:
        """把累计用量写入 llm_usage 表（append-only；调用方负责只调一次）。"""
        if not self.has_usage:
            return
        db = None
        try:
            db = await get_connection()
            await db.execute(
                "INSERT INTO llm_usage "
                "(usage_id, user_id, agent_type, model, input_tokens, output_tokens, cost_cny) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    agent_type,
                    self.model or settings.LLM_MODEL,
                    self.input_tokens,
                    self.output_tokens,
                    usage_cost_cny(self.input_tokens, self.output_tokens),
                ),
            )
            await db.commit()
            logger.info(
                "llm usage flushed: user=%s tokens=%d+%d cost=%.4f",
                user_id,
                self.input_tokens,
                self.output_tokens,
                usage_cost_cny(self.input_tokens, self.output_tokens),
            )
        except Exception:
            # 记账失败不应中断主流程
            logger.exception("flush llm usage failed")
        finally:
            if db is not None:
                await close_db(db)


def _extract_usage(response: Any) -> tuple[int | None, int | None, str]:
    """从 LLMResult 提取 (prompt_tokens, completion_tokens, model)。

    优先 `llm_output['token_usage']`（langchain-openai 标准字段）；
    其次回退到首个 generation 的 generation_info（部分 provider 走这里）。
    """
    model = ""
    llm_output = getattr(response, "llm_output", None) or {}
    if isinstance(llm_output, dict):
        model = str(llm_output.get("model_name") or "")
        usage = llm_output.get("token_usage")
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if prompt or completion:
                return int(prompt or 0), int(completion or 0), model

    # 回退：generation_info 里的 token_usage / usage / usage_metadata
    for gens in getattr(response, "generations", None) or []:
        for gen in gens or []:
            info = getattr(gen, "generation_info", None) or {}
            for key in ("token_usage", "usage", "usage_metadata"):
                u = info.get(key)
                if not isinstance(u, dict):
                    continue
                prompt = u.get("prompt_tokens") or u.get("input_tokens")
                completion = u.get("completion_tokens") or u.get("output_tokens")
                if prompt is not None and completion is not None:
                    return int(prompt), int(completion), model
    return None, None, model
