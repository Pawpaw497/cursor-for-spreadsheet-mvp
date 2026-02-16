"""Agent 路由：多轮推理 + 工具执行，支持同步与 SSE 流式输出。"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent import (
    AskClarificationAction,
    CallToolAction,
    action_kind,
    decision,
    initial_state_from_agent_project_request,
    run_agent_loop,
)
from app.agent.state import AgentState
from app.models import AgentProjectPlanRequest, PlanResponse

router = APIRouter(prefix="/api", tags=["agent"])


@router.post("/agent")
async def agent(req: AgentProjectPlanRequest):
    """
    使用 Agent 循环（多轮 LLM + 工具）生成执行计划。
    请求体与 /api/plan-project 相同；返回 plan 或 error/clarification。
    """
    state = initial_state_from_agent_project_request(req)
    final_state, action = await run_agent_loop(state)
    kind = action_kind(action)

    if kind == "output_plan":
        return PlanResponse(plan=action.payload)
    if kind == "finish":
        reason = (action.payload and action.payload.reason) or "unknown"
        raise HTTPException(  # type: ignore[unreachable]
            status_code=422,
            detail={"kind": "error", "reason": reason},
        )
    if kind == "ask_clarification":
        payload = action.payload
        return {
            "kind": "clarification",
            "plan": None,
            "clarification": {
                "question": payload.question,
                "options": payload.options,
                "context": payload.context,
            },
        }
    # call_tool 不应在循环结束后出现
    raise HTTPException(
        status_code=500,
        detail={
            "kind": "error",
            "reason": "unexpected_action_after_loop",
        },
    )


def _sse(event: str, data: dict) -> str:
    """构造单条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _agent_event_stream(state: AgentState) -> AsyncIterator[str]:
    """基于 AgentState 的流式事件：tool_call / tool_result / plan_done / finish / clarification。"""
    while True:
        if state.current_turn >= state.max_turns:
            yield _sse(
                "finish",
                {"reason": "max_turns", "state": state.to_dict()},
            )
            return

        state, action = await decision(state, use_tools=True)
        kind = action_kind(action)

        if kind == "output_plan":
            yield _sse("plan_done", {"plan": action.payload.model_dump(), "state": state.to_dict()})
            return

        if kind == "finish":
            reason = (action.payload and action.payload.reason) or "unknown"
            yield _sse("finish", {"reason": reason, "state": state.to_dict()})
            return

        if kind == "ask_clarification":
            payload: AskClarificationAction = action  # type: ignore[assignment]
            yield _sse(
                "clarification",
                {
                    "question": payload.payload.question,
                    "options": payload.payload.options,
                    "context": payload.payload.context,
                    "state": state.to_dict(),
                },
            )
            return

        if kind == "call_tool":
            payload: CallToolAction = action  # type: ignore[assignment]
            yield _sse(
                "tool_call",
                {
                    "tool": payload.payload.tool_name,
                    "args": payload.payload.tool_args,
                    "state": state.to_dict(),
                },
            )
            from app.agent.decision import run_tool_and_append_messages

            state = run_tool_and_append_messages(state, payload)
            yield _sse("tool_result", {"tool": payload.payload.tool_name, "state": state.to_dict()})

        await asyncio.sleep(0)


@router.post("/agent-stream")
async def agent_stream(req: AgentProjectPlanRequest):
    """
    SSE 流式 Agent：按步骤推送 tool_call / tool_result / plan_done / finish / clarification 事件。
    请求体与 /api/plan-project 相同。
    """
    state = initial_state_from_agent_project_request(req)
    return StreamingResponse(
        _agent_event_stream(state),
        media_type="text/event-stream",
    )
