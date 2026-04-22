"""decision 函数：Agent 的单步决策，输入 state，输出 (new_state, action)。"""
from __future__ import annotations

import json
from typing import Any

from app.agent.actions import (
    AgentAction,
    AskClarificationAction,
    CallToolAction,
    CallToolPayload,
    ClarificationPayload,
    FinishAction,
    FinishPayload,
    OutputPlanAction,
    action_kind,
)
from app.agent.state import AgentState
from app.logging_config import get_logger
from app.models.plan import Plan
from app.services.llm import call_llm, call_llm_with_tools
from app.services.prompts import (
    Message,
    ProjectPrompt,
    SpreadsheetPrompt,
    extract_json,
)
from app.services.tools import get_tools_spec_for_llm

log = get_logger("agent.decision")


def _build_messages_from_state(state: AgentState) -> list[Message]:
    """根据 state 组装 LLM 的 messages。首轮：system + user 内容；多轮：system + state.messages。"""
    if len(state.tables) == 1:
        prompt = SpreadsheetPrompt()
    else:
        prompt = ProjectPrompt()

    if not state.messages:
        # 首轮：用 tables + user_prompt 拼一条 user
        if len(state.tables) == 1:
            t = state.tables[0]
            user_content = prompt.build_user_content(
                state.user_prompt, t.schema, t.sample_rows
            )
        else:
            tables_data = [
                {
                    "name": t.name,
                    "schema": t.schema,
                    "sampleRows": t.sample_rows,
                }
                for t in state.tables
            ]
            user_content = prompt.build_user_content(
                state.user_prompt, tables_data
            )
        return [Message.system(prompt.system), Message.user(user_content)]

    # 多轮：system + 已有对话
    out: list[Message] = [Message.system(prompt.system)]
    for m in state.messages:
        out.append(Message(m["role"], m.get("content", "")))
    return out


def _build_messages_dict_from_state(state: AgentState) -> list[dict[str, Any]]:
    """供 call_llm_with_tools 使用：支持 tool_calls / tool 的 message 列表。"""
    if len(state.tables) == 1:
        prompt = SpreadsheetPrompt()
    else:
        prompt = ProjectPrompt()

    out: list[dict[str, Any]] = [{"role": "system", "content": prompt.system}]
    if not state.messages:
        if len(state.tables) == 1:
            t = state.tables[0]
            user_content = prompt.build_user_content(
                state.user_prompt, t.schema, t.sample_rows
            )
        else:
            tables_data = [
                {"name": t.name, "schema": t.schema, "sampleRows": t.sample_rows}
                for t in state.tables
            ]
            user_content = prompt.build_user_content(
                state.user_prompt, tables_data
            )
        out.append({"role": "user", "content": user_content})
        return out

    for m in state.messages:
        msg: dict[str, Any] = {"role": m.get("role", "user"), "content": m.get("content", "") or ""}
        if m.get("tool_calls") is not None:
            msg["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id") is not None:
            msg["tool_call_id"] = m["tool_call_id"]
        out.append(msg)
    return out


async def decision(
    state: AgentState,
    *,
    use_tools: bool = True,
) -> tuple[AgentState, AgentAction]:
    """
    Agent 的单步决策：根据当前 state 调 LLM，解析响应，返回 (更新后 state, action)。

    use_tools=True 时使用 call_llm_with_tools，模型可返回 tool_calls，此时返回 CallToolAction；
    否则或当返回 content 时解析为 Plan，返回 OutputPlanAction 或 FinishAction。
    """
    if state.current_turn >= state.max_turns:
        return (state, FinishAction(FinishPayload(reason="max_turns")))

    retry_user_suffix = "\nReturn ONLY JSON."
    content: str | None = None
    tool_calls: list[dict] | None = None

    if use_tools:
        messages_dict = _build_messages_dict_from_state(state)
        tools_spec = get_tools_spec_for_llm()
        try:
            content, tool_calls = await call_llm_with_tools(
                model_source=state.model_source,
                messages=messages_dict,
                tools=tools_spec,
                cloud_model_id=state.cloud_model_id,
                local_model_id=state.local_model_id,
            )
        except (ValueError, RuntimeError) as e:
            return (state, FinishAction(FinishPayload(reason=f"llm_error: {e!s}")))
        if tool_calls:
            tc = tool_calls[0]
            try:
                args = json.loads(tc.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            next_state = _state_after_turn(state)
            return (
                next_state,
                CallToolAction(
                    payload=CallToolPayload(
                        tool_name=tc.get("name", ""),
                        tool_args=args,
                        tool_call_id=tc.get("id"),
                    )
                ),
            )

    if content is None and not tool_calls:
        messages = _build_messages_from_state(state)
        try:
            content = await call_llm(
                model_source=state.model_source,
                messages=messages,
                cloud_model_id=state.cloud_model_id,
                local_model_id=state.local_model_id,
            )
        except (ValueError, RuntimeError) as e:
            return (state, FinishAction(FinishPayload(reason=f"llm_error: {e!s}")))

    if not (content or "").strip():
        return (
            state,
            FinishAction(FinishPayload(reason="empty_response")),
        )

    json_text = extract_json(content or "")
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        preview = (json_text[:200] + "…") if len(json_text) > 200 else json_text
        log.warning("agent decision json parse failed first_try err=%s preview=%s", e, preview)
        # 重试一次：追加 "Return ONLY JSON."
        if len(state.tables) == 1:
            prompt = SpreadsheetPrompt()
            t = state.tables[0]
            user_content = (
                prompt.build_user_content(
                    state.user_prompt, t.schema, t.sample_rows
                )
                + retry_user_suffix
            )
        else:
            prompt = ProjectPrompt()
            tables_data = [
                {
                    "name": t.name,
                    "schema": t.schema,
                    "sampleRows": t.sample_rows,
                }
                for t in state.tables
            ]
            user_content = (
                prompt.build_user_content(state.user_prompt, tables_data)
                + retry_user_suffix
            )
        retry_messages = [
            Message.system(prompt.system),
            Message.user(user_content),
        ]
        try:
            content = await call_llm(
                model_source=state.model_source,
                messages=retry_messages,
                cloud_model_id=state.cloud_model_id,
                local_model_id=state.local_model_id,
            )
        except (ValueError, RuntimeError) as e:
            return (
                state,
                FinishAction(FinishPayload(reason=f"llm_retry_error: {e!s}")),
            )
        json_text = extract_json(content)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            log.error(
                "agent decision json parse failed after_retry err=%s raw_preview=%s",
                e,
                (content[:200] + "…") if len(content) > 200 else content,
            )
            return (
                state,
                FinishAction(
                    FinishPayload(reason=f"invalid_json: {e!s}")
                ),
            )

    try:
        plan = Plan.model_validate(parsed)
    except Exception as e:
        log.error("agent decision plan validation failed err=%s", e)
        return (
            state,
            FinishAction(
                FinishPayload(reason=f"plan_validation_failed: {e!s}")
            ),
        )

    # 若多表场景下存在未指定 table 的列操作，则优先返回澄清请求
    clarify = _maybe_need_clarification(state, plan)
    if clarify is not None:
        next_state = _state_after_turn(state)
        return (next_state, clarify)

    next_state = _state_after_turn(state)
    return (next_state, OutputPlanAction(payload=plan))


# 多轮循环与 LangGraph 编排已迁移到 `orchestrator.py` 的
# `run_agent_orchestrated`；此处仅保留 `decision` 单步与工具辅助。


def _state_after_turn(state: AgentState) -> AgentState:
    """返回进入下一轮后的 state（current_turn + 1）。"""
    return AgentState(
        tables=state.tables,
        messages=state.messages,
        applied_plans_summary=state.applied_plans_summary,
        current_turn=state.current_turn + 1,
        max_turns=state.max_turns,
        user_prompt=state.user_prompt,
        model_source=state.model_source,
        cloud_model_id=state.cloud_model_id,
        local_model_id=state.local_model_id,
    )


def _maybe_need_clarification(
    state: AgentState,
    plan: Plan,
) -> AskClarificationAction | None:
    """
    简单澄清逻辑：
    - 多表场景下，若存在 add_column/transform_column 且未指定 table，则返回 ask_clarification。
    """
    table_names = [t.name for t in state.tables]
    if len(table_names) <= 1:
        return None

    ambiguous_steps: list[str] = []
    for idx, step in enumerate(plan.steps):
        action = getattr(step, "action", None)
        table = getattr(step, "table", None)
        if action in ("add_column", "transform_column") and not table:
            desc = f"#{idx}: {action}"
            col = getattr(step, "column", None) or getattr(step, "name", None)
            if col:
                desc += f" on {col}"
            ambiguous_steps.append(desc)

    if not ambiguous_steps:
        return None

    question = (
        "Multiple tables detected, but some steps do not specify which table "
        "to apply to. Which table should these steps target?"
    )
    context = (
        "Ambiguous steps:\n- " + "\n- ".join(ambiguous_steps)
        + "\nAvailable tables: " + ", ".join(table_names)
    )
    payload = ClarificationPayload(
        question=question,
        options=table_names,
        context=context,
    )
    return AskClarificationAction(payload=payload)


def run_tool_and_append_messages(
    state: AgentState, action: CallToolAction
) -> AgentState:
    """执行工具并将 assistant(tool_calls) + tool(result) 追加到 state.messages，返回新 state。"""
    from app.services.tools import run_tool

    payload = action.payload
    result = run_tool(
        tool_name=payload.tool_name,
        tool_args=payload.tool_args,
        tables=state.tables,
    )
    tid = payload.tool_call_id or "tool-0"
    assistant_tool_calls = [
        {
            "id": tid,
            "type": "function",
            "function": {
                "name": payload.tool_name,
                "arguments": json.dumps(payload.tool_args),
            },
        }
    ]
    new_messages = state.messages + [
        {"role": "assistant", "content": "", "tool_calls": assistant_tool_calls},
        {"role": "tool", "tool_call_id": tid, "content": result},
    ]
    return AgentState(
        tables=state.tables,
        messages=new_messages,
        applied_plans_summary=state.applied_plans_summary,
        current_turn=state.current_turn + 1,
        max_turns=state.max_turns,
        user_prompt=state.user_prompt,
        model_source=state.model_source,
        cloud_model_id=state.cloud_model_id,
        local_model_id=state.local_model_id,
    )
