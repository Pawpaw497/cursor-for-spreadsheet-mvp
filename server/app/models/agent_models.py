"""Agent 状态与预览记录模型：供路由、编排与 SSE 共享的结构化契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.models.table_models import DataContext, PrePlanContext

PreviewStatus = Literal["pending", "aborted", "committed", "revised"]
PreviewDecisionKind = Literal["confirm", "abort", "revise"]


class PreviewRecord(BaseModel):
    """一次可确认预览的紧凑元数据（不含全表快照）。

    ``plan`` 以 JSON 对象存储，避免与 ``Plan`` 类产生导入环；调用方可用
    ``Plan.model_validate(record.plan)`` 还原。
    """

    id: str
    plan: Dict[str, Any]
    diff: Dict[str, List[str]]
    new_tables: List[str] = Field(default_factory=list)
    status: PreviewStatus = "pending"
    user_decision: Optional[PreviewDecisionKind] = None
    user_decision_reason: Optional[str] = None
    execution_error: Optional[str] = None
    tables_fingerprint_at_preview: str = ""
    created_at: float
    resolved_at: Optional[float] = None


@dataclass
class TableContext:
    """单表上下文：schema + store 引用（table_id）。"""

    name: str
    schema: List[Dict[str, Any]]
    table_id: Optional[str] = None

    @classmethod
    def from_table_info(cls, t: Any) -> "TableContext":
        """由 ``TableInfo`` 构建上下文（duck-typing，避免顶层导入 plan）。"""
        return cls(name=t.name, schema=t.schema_, table_id=getattr(t, "tableRef", None))


class AgentState(BaseModel):
    """
    Agent 的显式状态：循环每轮更新，供 decision 读取、流式推送与记忆使用。

    字段说明：
    - tables: 当前项目下的表上下文（名、schema、样本行），工具与 prompt 基于此。
    - messages: 与 LLM 的对话历史（system/user/assistant + 工具结果），每轮追加。
    - applied_plans_summary: 本会话已应用计划的简短摘要，供多轮指代（如「把刚才那列删掉」）。
    - preview_history: 紧凑预览记录列表，不含行级快照。
    - revision_count: 预览失败后的自动修订次数，用于上限控制。
    - last_execution_error: 最近一次 dry-run / 执行错误摘要。
    - current_turn: 当前轮次（0-based），用于限轮与日志。
    - max_turns: 最大轮数，超过则强制结束。
    - user_prompt: 用户本轮/首次输入的自然语言请求。
    - model_source / cloud_model_id / local_model_id: LLM 调用配置。
    - pre_plan_context: pre_plan 子代理的表级裁剪结果（选中表/confidence/fallback 原因）；
      None 表示尚未跑过 pre_plan 或裁剪不适用（如单表/无表）。
    """

    tables: List[TableContext]
    # OpenAI-compatible tool messages use list/dict-valued fields (e.g. tool_calls).
    messages: List[Dict[str, Any]]
    applied_plans_summary: Optional[str] = None
    conversation: List[Dict[str, str]] = []
    preview_history: List[PreviewRecord] = Field(default_factory=list)
    revision_count: int = 0
    last_execution_error: Optional[str] = None
    current_turn: int = 0
    max_turns: int = 10
    user_prompt: str = ""
    model_source: Literal["cloud", "local"] = "cloud"
    cloud_model_id: Optional[str] = None
    local_model_id: Optional[str] = None
    request_context: Optional[Any] = None
    data_context: Optional[DataContext] = None
    pre_plan_context: Optional[PrePlanContext] = None

    def to_dict(self) -> Dict[str, Any]:
        """便于日志或 SSE 推送的字典表示（不含完整 messages 时可截断）。

        @return: 返回包含部分关键 Agent 状态统计的 dict，用于 SSE 事件或日志简报。
        """
        return {
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "tables_count": len(self.tables),
            "messages_count": len(self.messages),
            "applied_plans_summary": self.applied_plans_summary,
            "conversation_turns": len(self.conversation),
            "preview_history_count": len(self.preview_history),
            "revision_count": self.revision_count,
            "last_execution_error": self.last_execution_error,
            "has_data_context": self.data_context is not None,
        }


def initial_state_from_plan_request(req: Any) -> AgentState:
    """从单表计划请求构建初始 AgentState。"""
    from app.models.plan import PlanRequest

    if not isinstance(req, PlanRequest):
        req = PlanRequest.model_validate(req)
    tables = [
        TableContext(
            name="Sheet1",
            schema=req.schema_,
            table_id=None,
        )
    ]
    messages: List[Dict[str, Any]] = []
    return AgentState(
        tables=tables,
        messages=messages,
        user_prompt=req.prompt,
        model_source=req.modelSource or "cloud",
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )


def initial_state_from_project_request(req: Any) -> AgentState:
    """从多表/项目计划请求构建初始 AgentState。"""
    from app.models.plan import ProjectPlanRequest

    if not isinstance(req, ProjectPlanRequest):
        req = ProjectPlanRequest.model_validate(req)
    tables = [TableContext.from_table_info(t) for t in req.tables]
    messages: List[Dict[str, Any]] = []
    return AgentState(
        tables=tables,
        messages=messages,
        user_prompt=req.prompt,
        model_source=req.modelSource or "cloud",
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )


def _strip_clarification_prompt_suffix(prompt: str) -> str:
    """Remove client-side ``[Clarification]`` suffix when ``clarificationReply`` is set."""
    marker = "\n\n[Clarification]\n"
    idx = prompt.rfind(marker)
    if idx >= 0:
        return prompt[:idx]
    return prompt


def initial_state_from_agent_project_request(req: Any) -> AgentState:
    """从带历史的 Agent 请求构建初始 AgentState。"""
    from app.models.plan import AgentProjectPlanRequest

    if not isinstance(req, AgentProjectPlanRequest):
        req = AgentProjectPlanRequest.model_validate(req)
    tables = [TableContext.from_table_info(t) for t in req.tables]
    history_msgs: List[Dict[str, str]] = [
        {"role": turn.role, "content": turn.content} for turn in (req.history or [])
    ]
    from app.agent.memory_compaction import compact_agent_messages
    from app.agent.context_assembler import selection_context_user_message
    from app.agent.user_context import build_initial_user_message_from_tables

    request_context = req.context
    user_prompt = req.prompt
    clarification_reply = (req.clarificationReply or "").strip()
    if clarification_reply:
        from app.agent.clarification_telemetry import log_clarification_resolved

        log_clarification_resolved(
            reply=clarification_reply,
            turn_id=(req.clarificationTurnId or None),
        )
        user_prompt = _strip_clarification_prompt_suffix(user_prompt)
        if not (
            history_msgs
            and history_msgs[-1].get("role") == "user"
            and history_msgs[-1].get("content") == clarification_reply
        ):
            history_msgs = history_msgs + [
                {"role": "user", "content": clarification_reply}
            ]

    history_msgs = compact_agent_messages(
        history_msgs,
        applied_plans_summary=req.appliedPlansSummary,
        preserve_tail_count=0,
    )

    current_user = build_initial_user_message_from_tables(user_prompt, tables)
    transcript: List[Dict[str, Any]] = list(history_msgs)
    selection_msg = selection_context_user_message(request_context)
    if selection_msg is not None:
        transcript.append(selection_msg)
    transcript.append(current_user)
    return AgentState(
        tables=tables,
        messages=transcript,
        applied_plans_summary=req.appliedPlansSummary,
        conversation=transcript,
        user_prompt=user_prompt,
        model_source=req.modelSource or "cloud",
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
        preview_history=list(req.previewHistory or []),
        revision_count=int(req.revisionCount or 0),
        last_execution_error=req.lastExecutionError,
        request_context=request_context,
    )
