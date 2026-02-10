"""decision 函数：Agent 的单步决策，输入 state，输出 (new_state, action)。"""
from __future__ import annotations

import json

from app.agent.actions import (
    AgentAction,
    CallToolAction,
    FinishAction,
    FinishPayload,
    OutputPlanAction,
    action_kind,
)
from app.agent.state import AgentState
from app.models.plan import Plan
from app.services.llm import call_llm
from app.services.prompts import (
    Message,
    ProjectPrompt,
    SpreadsheetPrompt,
    extract_json,
)


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
        out.append(Message(m["role"], m["content"]))
    return out


async def decision(state: AgentState) -> tuple[AgentState, AgentAction]:
    """
    Agent 的单步决策：根据当前 state 调 LLM，解析响应，返回 (更新后 state, action)。

    当前实现：单轮生成 Plan（无 tool / clarification），成功返回 OutputPlanAction，
    解析失败时重试一次，仍失败则返回 FinishAction。
    """
    if state.current_turn >= state.max_turns:
        return (state, FinishAction(FinishPayload(reason="max_turns")))

    messages = _build_messages_from_state(state)
    retry_user_suffix = "\nReturn ONLY JSON."

    try:
        content = await call_llm(
            model_source=state.model_source,
            messages=messages,
            cloud_model_id=state.cloud_model_id,
            local_model_id=state.local_model_id,
        )
    except (ValueError, RuntimeError) as e:
        return (
            state,
            FinishAction(FinishPayload(reason=f"llm_error: {e!s}")),
        )

    json_text = extract_json(content)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
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
            return (
                state,
                FinishAction(
                    FinishPayload(reason=f"invalid_json: {e!s}")
                ),
            )

    try:
        plan = Plan.model_validate(parsed)
    except Exception as e:
        return (
            state,
            FinishAction(
                FinishPayload(reason=f"plan_validation_failed: {e!s}")
            ),
        )

    next_state = _state_after_turn(state)
    return (next_state, OutputPlanAction(payload=plan))


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


async def run_agent_loop(
    initial_state: AgentState,
) -> tuple[AgentState, AgentAction]:
    """
    Agent 循环：反复 decision → 根据 action 更新 state 或结束。

    - output_plan / finish / ask_clarification：立即返回 (state, action)，由调用方处理。
    - call_tool：占位执行（当前无工具实现），将占位结果写入 state.messages 后继续循环。
    """
    state = initial_state

    while True:
        if state.current_turn >= state.max_turns:
            return (state, FinishAction(FinishPayload(reason="max_turns")))

        state, action = await decision(state)
        kind = action_kind(action)

        if kind == "output_plan":
            return (state, action)
        if kind == "finish":
            return (state, action)
        if kind == "ask_clarification":
            return (state, action)

        if kind == "call_tool":
            # 占位：无工具实现时写入占位结果，便于后续接入真实 tools
            tool_msg = _run_tool_stub(action)
            new_messages = state.messages + [
                {"role": "assistant", "content": f"[tool_call: {action.payload.tool_name}]"},
                {"role": "user", "content": tool_msg},
            ]
            state = AgentState(
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


def _run_tool_stub(action: CallToolAction) -> str:
    """占位：工具未实现时返回说明文本，后续可替换为真实 tools 调用。"""
    return f"(Tool {action.payload.tool_name!r} not implemented yet. args={action.payload.tool_args})"
