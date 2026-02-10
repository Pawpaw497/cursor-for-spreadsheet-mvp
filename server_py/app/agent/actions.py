"""动作枚举：Agent 单步决策的离散输出，与 SSE 事件类型对齐。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from app.models.plan import Plan

# 动作类型字面量，便于循环与 SSE 使用
AgentActionKind = Literal[
    "call_tool",
    "output_plan",
    "ask_clarification",
    "finish",
]


@dataclass
class CallToolPayload:
    """call_tool 的 payload：工具名与参数。"""
    tool_name: str
    tool_args: Dict[str, Any]


@dataclass
class CallToolAction:
    """调用工具：执行后结果塞回 state.messages，再进入下一轮 decision。"""
    payload: CallToolPayload
    kind: Literal["call_tool"] = "call_tool"


@dataclass
class OutputPlanAction:
    """输出最终计划：循环结束，前端展示 Diff 并 Apply。"""
    payload: Plan
    kind: Literal["output_plan"] = "output_plan"


@dataclass
class ClarificationPayload:
    """ask_clarification 的 payload：问题与可选选项。"""
    question: str
    options: Optional[List[str]] = None
    context: Optional[str] = None


@dataclass
class AskClarificationAction:
    """请求用户澄清：前端展示问题与选项，用户回答后作为新 user 消息再请求。"""
    payload: ClarificationPayload
    kind: Literal["ask_clarification"] = "ask_clarification"


@dataclass
class FinishPayload:
    """finish 的 payload：结束原因（如达到 max_turns、用户取消）。"""
    reason: str = "done"


@dataclass
class FinishAction:
    """结束循环且无计划：如超轮、错误、用户取消。"""
    kind: Literal["finish"] = "finish"
    payload: Optional[FinishPayload] = None


AgentAction = Union[
    CallToolAction,
    OutputPlanAction,
    AskClarificationAction,
    FinishAction,
]


def action_kind(action: AgentAction) -> AgentActionKind:
    """返回动作的 kind，便于分支与 SSE 事件类型。"""
    return action.kind
