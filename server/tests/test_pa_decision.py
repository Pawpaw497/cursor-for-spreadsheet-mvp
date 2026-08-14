"""Pydantic AI pa_decision_step: mocked single-turn runs, Approach A (no in-PA tool exec)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import ToolCallPart

from app.agent.actions import (
    AskClarificationAction,
    CallToolAction,
    FinishAction,
    OutputPlanAction,
)
from app.agent.pa_decision import PaTurnResult, pa_decision_step
from app.models.agent_models import AgentState, TableContext
from app.models.plan import Plan


def _minimal_plan() -> Plan:
    return Plan.model_validate(
        {
            "intent": "add x",
            "steps": [
                {"action": "add_column", "name": "x", "expression": "1"},
            ],
        }
    )


def _turn_tools(*parts: ToolCallPart) -> PaTurnResult:
    return PaTurnResult(tool_parts=list(parts), text="", structured_plan=None)


def _turn_plan(plan: Plan | None = None) -> PaTurnResult:
    return PaTurnResult(
        tool_parts=[],
        text="",
        structured_plan=plan or _minimal_plan(),
    )


def _state(*, max_turns: int = 10, tables_count: int = 1) -> AgentState:
    tables = [
        TableContext(
            name="Sheet1",
            schema=[{"key": "a", "type": "string"}],
        )
    ]
    if tables_count > 1:
        tables.append(
            TableContext(name="Sheet2", schema=[{"key": "b", "type": "string"}])
        )
    return AgentState(
        tables=tables,
        messages=[],
        user_prompt="Add column",
        model_source="cloud",
        max_turns=max_turns,
    )


def test_pa_decision_max_turns() -> None:
    state = _state(max_turns=0)

    async def run() -> None:
        _, action = await pa_decision_step(state, use_tools=True)
        assert isinstance(action, FinishAction)
        assert action.payload and action.payload.reason == "max_turns"

    asyncio.run(run())


def test_pa_decision_tool_call_without_run_tool() -> None:
    state = _state()

    async def run() -> None:
        turn = _turn_tools(
            ToolCallPart(
                tool_name="get_schema",
                args={"table_name": "Sheet1"},
                tool_call_id="tc1",
            )
        )
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=turn,
        ), patch("app.services.tools.run_tool") as m_run:
            new_state, action = await pa_decision_step(state, use_tools=True)
            m_run.assert_not_called()
            assert isinstance(action, CallToolAction)
            assert action.payload.tool_name == "get_schema"
            assert new_state.current_turn == 1

    asyncio.run(run())


def test_pa_decision_output_plan_structured() -> None:
    state = _state()

    async def run() -> None:
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=_turn_plan(),
        ):
            _, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, OutputPlanAction)
            assert action.payload.intent == "add x"

    asyncio.run(run())


def test_pa_decision_empty_response() -> None:
    state = _state()

    async def run() -> None:
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=PaTurnResult([], "", None),
        ):
            _, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, FinishAction)
            assert action.payload and action.payload.reason == "empty_response"

    asyncio.run(run())


def test_pa_decision_final_result_failed_not_empty_response() -> None:
    state = _state()

    async def run() -> None:
        turn = PaTurnResult(
            tool_parts=[],
            text="",
            structured_plan=None,
            final_result_error="validation error preview",
        )
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=turn,
        ):
            _, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, FinishAction)
            assert action.payload
            reason = action.payload.reason or ""
            assert reason.startswith("plan_validation_failed:")
            assert "empty_response" not in reason

    asyncio.run(run())


def test_pa_decision_coerces_json_string_tool_args() -> None:
    state = _state()

    async def run() -> None:
        turn = _turn_tools(
            ToolCallPart(
                tool_name="validate_expression",
                args='{"expression": "row[\'a\']"}',
                tool_call_id="tc1",
            )
        )
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=turn,
        ), patch("app.services.tools.run_tool") as m_run:
            _, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, CallToolAction)
            assert action.payload.tool_name == "validate_expression"
            assert action.payload.tool_args["expression"] == "row['a']"
            m_run.assert_not_called()

    asyncio.run(run())


def test_pa_decision_invalid_tool_args_retries() -> None:
    state = _state(max_turns=5)
    calls = 0

    async def mock_turn(*_a: object, **_k: object) -> PaTurnResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _turn_tools(
                ToolCallPart(
                    tool_name="get_schema",
                    args="not-a-dict",  # type: ignore[arg-type]
                    tool_call_id="tc1",
                )
            )
        return _turn_plan()

    async def run() -> None:
        with patch("app.agent.pa_decision._run_pa_single_turn", side_effect=mock_turn):
            _, action = await pa_decision_step(state, use_tools=True)
            assert calls >= 2
            assert isinstance(action, OutputPlanAction)

    asyncio.run(run())


def test_pa_decision_clarification_multi_table() -> None:
    plan = Plan.model_validate(
        {
            "intent": "ambiguous",
            "steps": [{"action": "add_column", "name": "x", "expression": "1"}],
        }
    )
    state = _state(tables_count=2)

    async def run() -> None:
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=_turn_plan(plan),
        ):
            _, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, AskClarificationAction)

    asyncio.run(run())


def test_pa_decision_clarification_ambiguous_column() -> None:
    """sort_table on duplicate column name without table triggers clarification."""
    plan = Plan.model_validate(
        {
            "intent": "sort",
            "steps": [
                {
                    "action": "sort_table",
                    "column": "price",
                    "order": "ascending",
                }
            ],
        }
    )
    schema = [{"key": "price", "type": "number"}]
    state = AgentState(
        tables=[
            TableContext(name="Sheet1", schema=schema),
            TableContext(name="Sheet2", schema=schema),
        ],
        messages=[],
        user_prompt="sort price",
    )

    async def run() -> None:
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=_turn_plan(plan),
        ):
            _, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, AskClarificationAction)
            assert "multiple tables" in action.payload.question.lower()

    asyncio.run(run())


def test_pa_decision_tool_append_message_shape() -> None:
    """Parity with test_agent_message_shape: tool path seeds user context."""
    from app.agent.agent_helpers import run_tool_and_append_messages

    state = _state()

    async def run() -> None:
        turn = _turn_tools(
            ToolCallPart(
                tool_name="get_schema",
                args={},
                tool_call_id="call_test_1",
            )
        )
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            return_value=turn,
        ), patch("app.services.tools.run_tool", return_value="{}"):
            after_decision, action = await pa_decision_step(state, use_tools=True)
            assert isinstance(action, CallToolAction)
            final_state = run_tool_and_append_messages(after_decision, action)
            assert final_state.messages[0]["role"] == "user"
            assert state.user_prompt in final_state.messages[0]["content"]
            assert final_state.messages[-2].get("tool_calls") is not None
            assert final_state.messages[-1]["role"] == "tool"

    asyncio.run(run())


def test_pa_decision_finish_reason_error_fast_fails() -> None:
    """finish_reason='error' from upstream must return FinishAction quickly, not hang."""
    state = _state()

    async def run() -> None:
        with patch(
            "app.agent.pa_decision._run_pa_single_turn",
            side_effect=UnexpectedModelBehavior(
                "Model returned finish_reason='error' — upstream provider error"
            ),
        ):
            _, action = await pa_decision_step(state, use_tools=True)
        assert isinstance(action, FinishAction)
        assert action.payload is not None
        assert action.payload.reason.startswith("llm_error:")
        # 空串 reason 会通过 startswith——历史上的 `llm_error: ` 正是这么漏过去的。
        assert action.payload.reason[len("llm_error:") :].strip()

    asyncio.run(run())


def test_pa_decision_timeout_returns_finish_action() -> None:
    """asyncio.timeout guard must convert a hung turn into a FinishAction."""
    state = _state()

    async def _hang(*_args: object, **_kwargs: object) -> PaTurnResult:
        await asyncio.sleep(9999)
        return PaTurnResult([], "", None)

    async def run() -> None:
        import app.agent.pa_decision as mod

        orig_timeout = mod._PA_TURN_TIMEOUT_S
        mod._PA_TURN_TIMEOUT_S = 0.05
        try:
            with patch("app.agent.pa_decision._run_pa_single_turn", side_effect=_hang):
                _, action = await pa_decision_step(state, use_tools=True)
        finally:
            mod._PA_TURN_TIMEOUT_S = orig_timeout
        assert isinstance(action, FinishAction)
        assert action.payload is not None
        assert action.payload.reason.startswith("llm_error:")
        # 空串 reason 会通过 startswith——历史上的 `llm_error: ` 正是这么漏过去的。
        assert action.payload.reason[len("llm_error:") :].strip()

    asyncio.run(run())


def test_llm_error_reason_is_never_empty_by_category() -> None:
    """``_llm_error_reason`` 对各类上游故障都产出**非空且带类型**的 reason。

    分类不是为了好看：线上只有这一行字符串可归因。三类是 eval 里真实见过的形态
    （JSON body 不合法 / 模型行为异常 / 传输层故障），第四类是历史上产生空串的
    那个——裸 ``TimeoutError`` 的 ``str()`` 恰好为空。
    """
    import json as _json

    from pydantic_ai import UnexpectedModelBehavior as _UMB

    from app.agent.pa_decision import _PA_TURN_TIMEOUT_S, _llm_error_reason

    cases = [
        _json.JSONDecodeError("Expecting value", "", 0),
        _UMB("Invalid response from openrouter"),
        ValueError("boom"),
    ]
    for err in cases:
        reason = _llm_error_reason(err)
        detail = reason[len("llm_error:") :].strip()
        assert detail, f"{type(err).__name__} 产出了空 reason"
        assert type(err).__name__ in reason, f"reason 应带上异常类型：{reason!r}"

    # 裸 TimeoutError：str() 为空，必须由 fallback 兜出可读文案。
    timeout_reason = _llm_error_reason(TimeoutError())
    assert timeout_reason[len("llm_error:") :].strip()
    assert f"{_PA_TURN_TIMEOUT_S:.0f}s" in timeout_reason

    # 任何没有 message 的异常都不该退化成空串。
    assert _llm_error_reason(RuntimeError())[len("llm_error:") :].strip()
