"""动作枚举：Agent 单步决策的离散输出，与 SSE 事件类型对齐。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from app.models.agent_models import PreviewRecord
from app.models.plan import Plan

# 动作类型字面量，便于循环与 SSE 使用
AgentActionKind = Literal[
    "call_tool",
    "output_plan",
    "ask_clarification",
    "finish",
    "preview_ready",
]


@dataclass
class CallToolPayload:
    """call_tool 的 payload：工具名、参数，及回填给 LLM 的 tool_call_id。"""

    tool_name: str
    tool_args: Dict[str, Any]
    tool_call_id: Optional[str] = None


@dataclass
class CallToolAction:
    """调用工具：执行后结果塞回 state.messages，再进入下一轮 pa_decision_step。"""

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

    payload: Optional[FinishPayload] = None
    kind: Literal["finish"] = "finish"


@dataclass
class PreviewReadyPayload:
    """preview_ready：dry-run 成功后的终端动作，携带紧凑预览元数据。

    ``summary``：见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-preview-summary-wire，
    方案 A）。首包恒为 ``None``——摘要异步生成，不阻塞 preview_ready 本身
    （p1-preview-summary-async 落地前，本字段在两条响应路径上都只会是 None）。
    生成完成后通过独立 SSE 事件 ``preview_summary_ready``（payload:
    ``{"previewId": str, "summary": str}``）补发；sync ``/api/agent`` 客户端
    不 await 生成，接受首包无摘要。
    """

    plan: Plan
    preview: PreviewRecord
    warnings: Optional[List[str]] = None
    summary: Optional[str] = None


@dataclass
class PreviewReadyAction:
    """预览就绪：客户端在确认前不得将变更写入已提交数据源。"""

    payload: PreviewReadyPayload
    kind: Literal["preview_ready"] = "preview_ready"


AgentAction = Union[
    CallToolAction,
    OutputPlanAction,
    AskClarificationAction,
    FinishAction,
    PreviewReadyAction,
]


def action_kind(action: AgentAction) -> AgentActionKind:
    """返回动作的 kind，便于分支与 SSE 事件类型。"""
    return action.kind
