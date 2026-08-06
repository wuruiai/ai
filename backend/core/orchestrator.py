"""Agent 统一编排

Agent 调度和编排，支持单 Agent 和 Pipeline 模式。

Reference: §8.1, EduAgent orchestrator
"""

from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from backend.core.logger import get_logger

logger = get_logger(__name__)


class ExecutionMode(StrEnum):
    """执行模式"""

    SINGLE = "single"  # 单 Agent 直达
    PIPELINE = "pipeline"  # 多 Agent 串联


class AgentType(StrEnum):
    """Agent 类型"""

    KNOWLEDGE_QA = "knowledge_qa"  # 知识库问答
    DOCUMENT_ANALYSIS = "document_analysis"  # 文档分析
    WATER_EXPERT = "water_expert"  # 水利专家咨询


class AgentRequest(BaseModel):
    """Agent 请求"""

    user_id: str = Field(default="local_user", description="用户 ID")
    session_id: str = Field(..., description="会话 ID")
    agent_type: AgentType = Field(..., description="目标 Agent 类型")
    user_message: str = Field(..., description="用户输入")
    context: dict[str, Any] = Field(default_factory=dict, description="附加上下文")
    pipeline_mode: bool = Field(default=False, description="是否走 Pipeline")

    @property
    def thread_id(self) -> str:
        """线程 ID"""
        return f"user_{self.user_id}_session_{self.session_id}"


class AgentResponse(BaseModel):
    """Agent 响应"""

    success: bool = Field(..., description="执行是否成功")
    agent_type: AgentType = Field(..., description="实际执行的 Agent")
    content: str = Field(default="", description="文本响应")
    structured: dict[str, Any] | None = Field(default=None, description="结构化数据")
    fallback_used: bool = Field(default=False, description="是否触发降级")
    error_msg: str | None = Field(default=None, description="错误信息")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class PipelineResult(BaseModel):
    """Pipeline 结果"""

    steps: list[AgentResponse] = Field(default_factory=list)
    combined: dict[str, Any] = Field(default_factory=dict)


class Orchestrator:
    """Agent 编排器"""

    def __init__(self):
        self._agent_graphs: dict[AgentType, Any] = {}

        # Pipeline 定义
        self._pipelines: dict[str, list[AgentType]] = {
            "document_qa": [
                AgentType.DOCUMENT_ANALYSIS,
                AgentType.KNOWLEDGE_QA,
            ],
            "expert_consultation": [
                AgentType.KNOWLEDGE_QA,
                AgentType.WATER_EXPERT,
            ],
        }

        logger.info("Orchestrator initialized")

    def _get_agent_graph(self, agent_type: AgentType) -> Any:
        """懒加载 Agent 图"""
        if agent_type not in self._agent_graphs:
            if agent_type == AgentType.KNOWLEDGE_QA:
                from backend.agents.knowledge_qa.graph import build_knowledge_qa_graph

                self._agent_graphs[agent_type] = build_knowledge_qa_graph()

            elif agent_type == AgentType.DOCUMENT_ANALYSIS:
                from backend.agents.document_analysis.graph import create_document_analysis_graph

                self._agent_graphs[agent_type] = create_document_analysis_graph()

            elif agent_type == AgentType.WATER_EXPERT:
                from backend.agents.water_expert.graph import build_water_expert_graph

                self._agent_graphs[agent_type] = build_water_expert_graph()

            else:
                raise ValueError(f"Unknown AgentType: {agent_type}")

            logger.info("Agent graph loaded: %s", agent_type.value)

        return self._agent_graphs[agent_type]

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """统一请求处理入口"""
        logger.info(
            "Orchestrator.handle: agent=%s, pipeline=%s",
            request.agent_type.value,
            request.pipeline_mode,
        )

        try:
            if request.pipeline_mode:
                result = await self._run_pipeline(request)
                return self._aggregate_pipeline(result, request)
            else:
                return await self._run_single_agent(request)

        except Exception as e:
            logger.exception("Orchestrator.handle failed: %s", e)
            return AgentResponse(
                success=False,
                agent_type=request.agent_type,
                content="系统处理请求时遇到问题，请稍后再试。",
                error_msg=str(e),
            )

    async def _run_single_agent(self, request: AgentRequest) -> AgentResponse:
        """单 Agent 直达模式"""
        graph = self._get_agent_graph(request.agent_type)

        # 多轮记忆：若 context 带 history（历史消息列表），放最前，当前提问追加在后
        history = request.context.get("history") or []
        messages = [*history, HumanMessage(content=request.user_message)]

        initial_state = {
            "messages": messages,
            "student_id": request.user_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            **{k: v for k, v in request.context.items() if k != "history"},
        }

        config = {
            "configurable": {
                "thread_id": request.thread_id,
            }
        }

        result_state = await graph.ainvoke(initial_state, config=config)

        # 提取响应
        last_message = result_state["messages"][-1]
        content = last_message.content if hasattr(last_message, "content") else str(last_message)

        return AgentResponse(
            success=True,
            agent_type=request.agent_type,
            content=content,
            structured=result_state.get("structured_output"),
            fallback_used=result_state.get("fallback_used", False),
        )

    async def _run_pipeline(self, request: AgentRequest) -> PipelineResult:
        """多 Agent 串联 Pipeline 模式"""
        pipeline_key = request.context.get("pipeline_key", "document_qa")

        if pipeline_key not in self._pipelines:
            raise ValueError(f"Unknown pipeline: {pipeline_key}")

        agent_sequence = self._pipelines[pipeline_key]
        result = PipelineResult()
        current_context = dict(request.context)

        for idx, agent_type in enumerate(agent_sequence):
            step_request = AgentRequest(
                user_id=request.user_id,
                session_id=f"{request.session_id}_step{idx}",
                agent_type=agent_type,
                user_message=request.user_message,
                context=current_context,
                pipeline_mode=False,
            )

            logger.info("Pipeline step %d/%d: %s", idx + 1, len(agent_sequence), agent_type.value)

            step_response = await self._run_single_agent(step_request)
            result.steps.append(step_response)

            if not step_response.success:
                logger.warning("Pipeline step %d failed", idx + 1)
                break

            # 上下文传递
            if step_response.structured:
                current_context[f"{agent_type.value}_result"] = step_response.structured

        return result

    def _aggregate_pipeline(
        self,
        pipeline_result: PipelineResult,
        request: AgentRequest,
    ) -> AgentResponse:
        """聚合 Pipeline 结果"""
        combined = {}
        all_contents = []
        any_success = False

        for idx, step in enumerate(pipeline_result.steps):
            step_key = f"step_{idx + 1}"
            combined[step_key] = {
                "agent_type": step.agent_type.value,
                "success": step.success,
                "structured": step.structured,
            }
            if step.success:
                any_success = True
                if step.content:
                    all_contents.append(step.content)

        return AgentResponse(
            success=any_success,
            agent_type=request.agent_type,
            content="\n\n---\n\n".join(all_contents),
            structured=combined,
            fallback_used=any(s.fallback_used for s in pipeline_result.steps),
        )


# 单例
_orchestrator_instance: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """获取 Orchestrator 单例"""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = Orchestrator()
    return _orchestrator_instance


# 模块级单例（向后兼容，允许 from backend.core.orchestrator import orchestrator）
orchestrator: Orchestrator = get_orchestrator()
