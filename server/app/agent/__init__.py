"""Agent 骨架：状态、动作、决策与 LangGraph 编排。"""
from app.agent.actions import (
    AgentAction,
    AgentActionKind,
    AskClarificationAction,
    CallToolAction,
    CallToolPayload,
    ClarificationPayload,
    FinishAction,
    FinishPayload,
    OutputPlanAction,
    action_kind,
)
from app.agent.decision import decision
from app.agent.orchestrator import (
    run_agent_orchestrated,
    stream_agent_events,
)
from app.agent.state import (
    AgentState,
    TableContext,
    initial_state_from_agent_project_request,
    initial_state_from_plan_request,
    initial_state_from_project_request,
)

# 向后兼容：历史代码与测试均使用 `run_agent_loop` 名称。
run_agent_loop = run_agent_orchestrated

__all__ = [
    "AgentState",
    "TableContext",
    "initial_state_from_plan_request",
    "initial_state_from_project_request",
    "initial_state_from_agent_project_request",
    "AgentAction",
    "AgentActionKind",
    "CallToolAction",
    "CallToolPayload",
    "OutputPlanAction",
    "AskClarificationAction",
    "ClarificationPayload",
    "FinishAction",
    "FinishPayload",
    "action_kind",
    "decision",
    "run_agent_orchestrated",
    "run_agent_loop",
    "stream_agent_events",
]
