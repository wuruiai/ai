"""LLM token / 成本记账（G3.1）

TokenStreamHandler 只负责逐 token 推送；UsageCollector 作为同一回调链上的
兄弟 handler，在 `on_llm_end` 里捕获 usage（prompt/completion tokens），
由调用方在请求结束时 `flush()` 到 llm_usage 表，供 `/admin/usage` 聚合展示。

落地位置：chat.py 创建 `[stream_handler, usage_collector]` 一并塞入
`llm_callbacks`，经 orchestrator → state → 各 LLM 节点回调链收集。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

from backend.config import settings
from backend.core.logger import get_logger
from backend.core.metrics import record_llm_call
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
        # 流式路径：langchain-openai 1.x 在 on_llm_end 聚合结果里不带 usage，
        # 改为从流式 chunk 的 usage_metadata 捕获（每轮调用至多一个 chunk 带 usage）
        self._stream_usage: tuple[int, int, str] | None = None
        # G10.9 M10：单次调用开始时间（on_llm_start 置位，on_llm_end 计算延迟后清空）
        self._call_started: float | None = None

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        """记录调用开始时间，供 on_llm_end 计算该次 LLM 延迟（G10.9 M10）。"""
        self._call_started = time.perf_counter()

    @property
    def has_usage(self) -> bool:
        return self.input_tokens > 0 or self.output_tokens > 0

    def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
        """流式 chunk 携带 usage_metadata 时暂存（该调用本轮的总用量）。

        - 逐 token 回调由 TokenStreamHandler 消费，这里只关心 chunk 上带的总 usage；
        - 回调的 chunk 是 ChatGenerationChunk 包装，usage_metadata 在其 `.message`
          （AIMessageChunk）上，需先 unwrap；
        - 每个 OpenAI 兼容流至多一个 chunk 带 usage（通常是最后一个）；
        - chunk 上无 model 字段，留空由 flush 落到 settings.LLM_MODEL。
        """
        # ChatGenerationChunk → AIMessageChunk（无包装时退化为 chunk 本身）
        msg = getattr(chunk, "message", chunk)
        usage = getattr(msg, "usage_metadata", None)
        if not isinstance(usage, dict):
            return
        prompt = usage.get("input_tokens")
        completion = usage.get("output_tokens")
        if prompt is None or completion is None:
            return
        model = getattr(msg, "model", "") or ""
        self._stream_usage = (int(prompt), int(completion), model)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        prompt, completion, model = _extract_usage(response)
        if prompt is None:
            # 聚合结果无 usage（流式路径）时回退到 chunk 捕获值；用完即清
            if self._stream_usage is not None:
                prompt, completion, model = self._stream_usage
                self._stream_usage = None
            else:
                # 无 usage 信息（异常/降级/测试 stub）：仍打点次数+延迟，不累加记账
                self._record_metric(prompt or 0, completion or 0, model)
                return
        self.input_tokens += prompt
        self.output_tokens += completion
        if model:
            self.model = model
        self._record_metric(prompt, completion, model)

    def _record_metric(self, prompt: int, completion: int, model: str) -> None:
        """G10.9 M10：逐调用打点 Prometheus LLM 指标（延迟/token/成本）。"""
        started = self._call_started
        self._call_started = None
        duration_s = (time.perf_counter() - started) if started is not None else 0.0
        try:
            record_llm_call(
                model or settings.LLM_MODEL,
                prompt,
                completion,
                usage_cost_cny(prompt, completion),
                duration_s,
            )
        except Exception:
            # exc_info=True 已把异常记入日志，BLE001 豁免（非盲目吞掉）
            logger.warning("record llm metric failed", exc_info=True)

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
