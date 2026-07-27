"""Agent 路由：多轮推理 + 工具执行，支持同步与 SSE 流式输出。"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, cast

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent import (
    action_kind,
    initial_state_from_agent_project_request,
    run_agent_orchestrated,
    stream_agent_events,
)
from app.api.routes.plan import _http_exception_from_runtime
from app.agent.actions import (
    AskClarificationAction,
    FinishAction,
    OutputPlanAction,
    PreviewReadyAction,
)
from app.agent.state import AgentState
from app.agent.preview_telemetry import log_preview_cap_hit, log_preview_revision
from app.logging_config import get_logger
from app.models import AgentProjectPlanRequest, Plan, PlanResponse
from app.models.plan import plan_to_wire_dict, preview_record_to_wire_dict
from app.models.plan import ConversationTurn
from app.services.agent_preview import (
    MAX_AGENT_PREVIEW_REVISIONS,
    classify_stale_preview_reason,
    dry_run_plan_on_tables,
    execution_result_to_execute_plan_response,
    fingerprint_execution_tables,
    last_pending_preview,
    merge_preview_history_mark_aborted,
    merge_preview_history_mark_committed,
    merge_preview_history_mark_revised,
    project_state_to_execution_tables,
    resolve_execution_tables_for_agent_request,
)
from app.services.plan_executor import TableData, apply_project_plan
from app.services.projects import project_store
from app.services.tools import get_tools_spec_for_llm

router = APIRouter(prefix="/api", tags=["agent"])
log = get_logger("api.agent")


def _resolve_project_tables_optional(req: AgentProjectPlanRequest) -> Optional[Dict[str, TableData]]:
    """若请求携带 ``projectId`` 且项目存在，返回执行引擎表字典。

    @param req: Agent 请求。
    @return: 表字典；项目不存在时返回 ``None``（由调用方决定如何报错）。
    """
    if not req.projectId:
        return None
    state = project_store.get_project(req.projectId)
    if not state:
        return None
    return project_state_to_execution_tables(state)


async def _run_agent_core(
    req: AgentProjectPlanRequest,
    *,
    preview_lifecycle: bool,
    execution_tables: Optional[Dict[str, TableData]],
) -> tuple[AgentState, Any]:
    """构建初始状态并执行编排（含可选预览后处理）。"""
    state = initial_state_from_agent_project_request(req)
    log.info(
        "agent start tables=%d history_turns=%d max_turns=%d model_source=%s prompt_len=%d tools_spec_count=%d preview_lifecycle=%s",
        len(state.tables),
        len(req.history or []),
        state.max_turns,
        state.model_source,
        len(req.prompt or ""),
        len(get_tools_spec_for_llm()),
        preview_lifecycle,
    )
    return await run_agent_orchestrated(
        state,
        preview_lifecycle=preview_lifecycle,
        execution_tables=execution_tables,
    )


def _preview_record_plan_model(rec: Any) -> Plan:
    """从 ``PreviewRecord`` 还原 ``Plan`` 对象。"""
    from app.models.agent_models import PreviewRecord

    if isinstance(rec, PreviewRecord):
        return Plan.model_validate(rec.plan)
    return Plan.model_validate(rec.get("plan") if isinstance(rec, dict) else rec.plan)


@router.post("/agent")
async def agent(req: AgentProjectPlanRequest):
    """
    使用 Agent 循环（多轮 LLM + 工具）生成执行计划。

    请求体为 prompt + tables（含 tableRef），并支持可选 ``previewLifecycle`` 与预览决策字段。
    """
    preview_lifecycle = bool(req.previewLifecycle)
    project_snapshot = _resolve_project_tables_optional(req)
    if req.projectId and project_snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"kind": "error", "reason": f"project_not_found:{req.projectId}"},
        )

    execution_tables = resolve_execution_tables_for_agent_request(
        req,
        project_tables=project_snapshot,
    )

    # --- 显式决策路径（不进入 LangGraph）---
    if req.previewDecision == "abort":
        if not req.previewId:
            raise HTTPException(
                status_code=400,
                detail={"kind": "error", "reason": "preview_id_required"},
            )
        hist = merge_preview_history_mark_aborted(
            list(req.previewHistory or []),
            req.previewId,
            req.revisionMessage,
        )
        return {
            "kind": "preview_aborted",
            "previewHistory": [preview_record_to_wire_dict(h) for h in hist],
        }

    if req.previewDecision == "confirm":
        if not req.previewId:
            raise HTTPException(
                status_code=400,
                detail={"kind": "error", "reason": "preview_id_required"},
            )
        plan_obj = req.commitPlan
        if plan_obj is None:
            for h in req.previewHistory or []:
                hid = h.id if hasattr(h, "id") else h.get("id")
                if hid == req.previewId:
                    plan_obj = _preview_record_plan_model(h)
                    break
        if plan_obj is None:
            raise HTTPException(
                status_code=400,
                detail={"kind": "error", "reason": "commit_plan_required"},
            )
        preview_rec = None
        for h in req.previewHistory or []:
            hid = h.id if hasattr(h, "id") else h.get("id")
            if hid == req.previewId:
                preview_rec = h
                break
        if preview_rec is None:
            raise HTTPException(
                status_code=400,
                detail={"kind": "error", "reason": "preview_not_in_history"},
            )
        fp_expected = (
            preview_rec.tables_fingerprint_at_preview
            if hasattr(preview_rec, "tables_fingerprint_at_preview")
            else preview_rec.get("tables_fingerprint_at_preview")
        )
        current_tables = resolve_execution_tables_for_agent_request(
            req,
            project_tables=project_snapshot,
        )
        if current_tables is None:
            raise HTTPException(
                status_code=400,
                detail={"kind": "error", "reason": "execution_tables_required_for_confirm"},
            )
        fp_now = fingerprint_execution_tables(current_tables)
        if fp_expected and fp_now != fp_expected:
            stale_reason = classify_stale_preview_reason(str(fp_expected), current_tables)
            raise HTTPException(
                status_code=409,
                detail={
                    "kind": "error",
                    "reason": "stale_preview",
                    "staleReason": stale_reason,
                    "expectedFingerprint": fp_expected,
                    "currentFingerprint": fp_now,
                },
            )
        result = apply_project_plan(current_tables, plan_obj)
        hist = merge_preview_history_mark_committed(
            list(req.previewHistory or []),
            req.previewId,
        )
        if req.projectId:
            persisted_tables: Dict[str, Dict[str, Any]] = {}
            for name, t in result.tables.items():
                persisted_tables[name] = {
                    "name": t.name,
                    "rows": t.rows,
                    "schema": [{"key": c.key, "type": c.type} for c in t.schema],
                }
            project_store.update_tables(req.projectId, persisted_tables)

        exec_resp = execution_result_to_execute_plan_response(result)
        return {
            "kind": "committed",
            "executeResult": exec_resp.model_dump(by_alias=True),
            "previewHistory": [preview_record_to_wire_dict(h) for h in hist],
        }

    if req.previewDecision == "revise":
        if req.revisionCount >= MAX_AGENT_PREVIEW_REVISIONS:
            pending = last_pending_preview(list(req.previewHistory or []))
            if pending is not None:
                log_preview_cap_hit(
                    revision_count=req.revisionCount,
                    preview_id=pending.id,
                    source="user_revise",
                )
                plan_obj = Plan.model_validate(pending.plan)
                return {
                    "kind": "preview_ready",
                    "plan": plan_to_wire_dict(plan_obj),
                    "preview": preview_record_to_wire_dict(pending),
                    "previewHistory": [
                        preview_record_to_wire_dict(h)
                        for h in (req.previewHistory or [])
                    ],
                    "warnings": [
                        f"Preview revision limit ({MAX_AGENT_PREVIEW_REVISIONS}) reached.",
                        "You may confirm this preview, abort, or rephrase your request.",
                    ],
                    "state": {"revision_count": req.revisionCount},
                    "summary": None,
                }
            raise HTTPException(
                status_code=429,
                detail={
                    "kind": "error",
                    "reason": "preview_revision_cap",
                    "max": MAX_AGENT_PREVIEW_REVISIONS,
                },
            )
        if not req.previewId or not (req.revisionMessage or "").strip():
            raise HTTPException(
                status_code=400,
                detail={"kind": "error", "reason": "preview_id_and_revision_message_required"},
            )
        log_preview_revision(
            revision_count=req.revisionCount + 1,
            preview_id=req.previewId,
            source="user_revise",
            reason_preview=(req.revisionMessage or "").strip(),
        )
        hist = merge_preview_history_mark_revised(
            list(req.previewHistory or []),
            req.previewId,
        )
        extra = (req.revisionMessage or "").strip()
        new_history = list(req.history or [])
        new_history.append(
            ConversationTurn(
                role="user",
                content=f"User revision request: {extra}",
            )
        )
        revised_req = req.model_copy(
            update={
                "history": new_history,
                "previewHistory": hist,
                "revisionCount": req.revisionCount + 1,
                "previewDecision": None,
                "previewId": None,
                "revisionMessage": None,
            }
        )
        final_state, action = await _run_agent_core(
            revised_req,
            preview_lifecycle=preview_lifecycle,
            execution_tables=execution_tables,
        )
        return _map_agent_result_to_response(
            final_state,
            action,
        )

    # --- 默认：生成路径 ---
    final_state, action = await _run_agent_core(
        req,
        preview_lifecycle=preview_lifecycle,
        execution_tables=execution_tables,
    )
    return _map_agent_result_to_response(
        final_state,
        action,
    )


def _map_agent_result_to_response(
    final_state: AgentState,
    action: Any,
) -> Any:
    """将终端 ``AgentAction`` 映射为 HTTP JSON 响应。"""
    kind = action_kind(action)
    log.info(
        "agent done kind=%s current_turn=%d summary=%s",
        kind,
        final_state.current_turn,
        final_state.to_dict(),
    )

    if kind == "preview_ready":
        pra = cast(PreviewReadyAction, action)
        resp: dict[str, Any] = {
            "kind": "preview_ready",
            "plan": plan_to_wire_dict(pra.payload.plan),
            "preview": preview_record_to_wire_dict(pra.payload.preview),
            "previewHistory": [
                preview_record_to_wire_dict(h) for h in final_state.preview_history
            ],
            "state": final_state.to_dict(),
            # p1-preview-summary-wire 方案 A：首包恒为 None，见 PreviewReadyPayload docstring。
            "summary": pra.payload.summary,
        }
        if pra.payload.warnings:
            resp["warnings"] = pra.payload.warnings
        return resp

    if kind == "output_plan":
        return PlanResponse(plan=cast(OutputPlanAction, action).payload)

    if kind == "finish":
        reason = (action.payload and action.payload.reason) or "unknown"
        if isinstance(reason, str) and reason.startswith("llm_error:"):
            inner = reason[len("llm_error:") :].strip()
            if inner.startswith("[502]") or "AUTH_ERROR:" in inner:
                raise _http_exception_from_runtime(RuntimeError(inner))
            if "OPENROUTER_API_KEY missing" in inner or inner.startswith(
                "Unknown modelSource:"
            ):
                raise HTTPException(status_code=400, detail=f"[400] {inner}")
        raise HTTPException(
            status_code=422,
            detail={"kind": "error", "reason": reason},
        )

    if kind == "ask_clarification":
        payload = cast(AskClarificationAction, action).payload
        return {
            "kind": "clarification",
            "plan": None,
            "clarification": {
                "question": payload.question,
                "options": payload.options,
                "context": payload.context,
            },
        }

    raise HTTPException(
        status_code=500,
        detail={
            "kind": "error",
            "reason": "unexpected_action_after_loop",
        },
    )


async def _agent_event_stream(
    state: AgentState,
    *,
    preview_lifecycle: bool,
    execution_tables: Optional[Dict[str, TableData]],
) -> AsyncIterator[str]:
    """代理到 orchestrator 的 SSE 序列（事件名与字段保持后向兼容）。"""
    async for chunk in stream_agent_events(
        state,
        preview_lifecycle=preview_lifecycle,
        execution_tables=execution_tables,
    ):
        yield chunk


@router.post("/agent-stream")
async def agent_stream(req: AgentProjectPlanRequest):
    """
    SSE 流式 Agent：按步骤推送 tool_call / tool_result / plan_done / finish / clarification 事件。

    当 ``previewLifecycle`` 为真且可解析执行表时，额外发送 ``preview_ready``（仍发送 ``plan_done``）。
    """
    preview_lifecycle = bool(req.previewLifecycle)
    project_snapshot = _resolve_project_tables_optional(req)
    if req.projectId and project_snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"kind": "error", "reason": f"project_not_found:{req.projectId}"},
        )
    execution_tables = resolve_execution_tables_for_agent_request(
        req,
        project_tables=project_snapshot,
    )

    state = initial_state_from_agent_project_request(req)
    log.info(
        "agent_stream start tables=%d history_turns=%d max_turns=%d model_source=%s prompt_len=%d preview_lifecycle=%s",
        len(state.tables),
        len(req.history or []),
        state.max_turns,
        state.model_source,
        len(req.prompt or ""),
        preview_lifecycle,
    )
    return StreamingResponse(
        _agent_event_stream(
            state,
            preview_lifecycle=preview_lifecycle,
            execution_tables=execution_tables,
        ),
        media_type="text/event-stream",
    )
