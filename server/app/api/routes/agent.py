"""Agent 路由：多轮推理 + 工具执行，支持同步与 SSE 流式输出。"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent import (
    action_kind,
    initial_state_from_agent_project_request,
    run_agent_orchestrated,
    stream_agent_events,
)
from app.agent.state import AgentState
from app.logging_config import get_logger
from app.models import AgentProjectPlanRequest, PlanResponse
from app.services.tools import get_tools_spec_for_llm

router = APIRouter(prefix="/api", tags=["agent"])
log = get_logger("api.agent")


@router.post("/agent")
async def agent(req: AgentProjectPlanRequest):
    """
    使用 Agent 循环（多轮 LLM + 工具）生成执行计划。
    请求体与 /api/plan-project 相同；返回 plan 或 error/clarification。
    """
    state = initial_state_from_agent_project_request(req)
    log.info(
        "agent start tables=%d history_turns=%d max_turns=%d model_source=%s prompt_len=%d tools_spec_count=%d",
        len(state.tables),
        len(req.history or []),
        state.max_turns,
        state.model_source,
        len(req.prompt or ""),
        len(get_tools_spec_for_llm()),
    )
    final_state, action = await run_agent_orchestrated(state)
    kind = action_kind(action)
    log.info(
        "agent done kind=%s current_turn=%d summary=%s",
        kind,
        final_state.current_turn,
        final_state.to_dict(),
    )

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


async def _agent_event_stream(state: AgentState) -> AsyncIterator[str]:
    """代理到 orchestrator 的 SSE 序列（事件名与字段不变）。"""
    async for chunk in stream_agent_events(state):
        yield chunk


@router.post("/agent-stream")
async def agent_stream(req: AgentProjectPlanRequest):
    """
    SSE 流式 Agent：按步骤推送 tool_call / tool_result / plan_done / finish / clarification 事件。
    请求体与 /api/plan-project 相同。
    """
    state = initial_state_from_agent_project_request(req)
    log.info(
        "agent_stream start tables=%d history_turns=%d max_turns=%d model_source=%s prompt_len=%d",
        len(state.tables),
        len(req.history or []),
        state.max_turns,
        state.model_source,
        len(req.prompt or ""),
    )
    return StreamingResponse(
        _agent_event_stream(state),
        media_type="text/event-stream",
    )
