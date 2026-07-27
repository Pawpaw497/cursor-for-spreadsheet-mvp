"""Sync orchestrator terminal ordering and sync/SSE preview_ready contract parity."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.actions import (
    AskClarificationAction,
    CallToolAction,
    CallToolPayload,
    ClarificationPayload,
    FinishAction,
    FinishPayload,
    OutputPlanAction,
    PreviewReadyAction,
    action_kind,
)
from app.agent.orchestrator import run_agent_orchestrated, stream_agent_events
from app.api.routes.agent import _map_agent_result_to_response
from app.models.agent_models import AgentState, TableContext
from app.models.plan import Plan
from app.services.plan_executor import SchemaCol, TableData


def _agent_state() -> AgentState:
    return AgentState(
        tables=[
            TableContext(
                name="Sheet1",
                schema=[{"key": "a", "type": "string"}],
            )
        ],
        messages=[],
        user_prompt="test",
        max_turns=10,
    )


def _tables() -> dict[str, TableData]:
    return {
        "Sheet1": TableData(
            name="Sheet1",
            rows=[{"a": "x"}],
            schema=[SchemaCol(key="a", type="string")],
        )
    }


def _good_plan() -> Plan:
    return Plan.model_validate(
        {
            "intent": "ok",
            "steps": [{"action": "add_column", "name": "x", "expression": "1"}],
        }
    )


def _parse_sse_events(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        m_event = re.search(r"event: (\w+)", chunk)
        m_data = re.search(r"data: (.+)", chunk)
        if m_event and m_data:
            out.append((m_event.group(1), json.loads(m_data.group(1))))
    return out


def _mock_graph_returning_terminal(action) -> AsyncMock:
    compiled = AsyncMock()

    async def fake_ainvoke(init):
        agent_dump = init["agent"]
        k = action_kind(action)
        if k == "output_plan":
            ser = {"kind": "output_plan", "plan": action.payload.model_dump()}
        elif k == "ask_clarification":
            p = action.payload
            ser = {
                "kind": "ask_clarification",
                "question": p.question,
                "options": p.options,
                "context": p.context,
            }
        elif k == "finish":
            ser = {"kind": "finish", "reason": action.payload.reason}
        else:
            raise ValueError(f"unsupported terminal action {k}")
        return {"agent": agent_dump, "scratch": {"ser_action": ser}}

    compiled.ainvoke = fake_ainvoke
    return compiled


@pytest.mark.parametrize(
    "terminal_action,expected_kind",
    [
        (OutputPlanAction(payload=_good_plan()), "output_plan"),
        (
            AskClarificationAction(
                payload=ClarificationPayload(
                    question="Which sheet?",
                    options=["Sheet1"],
                    context="ambiguous",
                )
            ),
            "ask_clarification",
        ),
        (FinishAction(FinishPayload(reason="user_stop")), "finish"),
    ],
    ids=["plan", "clarification", "finish"],
)
def test_sync_orchestrator_terminal_action_kinds(
    terminal_action, expected_kind: str
) -> None:
    async def run() -> None:
        state = _agent_state()
        with patch(
            "app.agent.orchestrator.get_compiled_agent_graph",
            return_value=_mock_graph_returning_terminal(terminal_action),
        ):
            _final, action = await run_agent_orchestrated(
                state, preview_lifecycle=False
            )
        assert action_kind(action) == expected_kind

    asyncio.run(run())


def test_sync_and_sse_preview_ready_payload_parity() -> None:
    """同一 mocked plan：sync ``_map_agent_result_to_response`` 与 SSE ``preview_ready`` 含等价 previewHistory。"""

    async def run() -> None:
        state = _agent_state()
        tables = _tables()
        plan = _good_plan()

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=plan)

        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ), patch(
            "app.agent.orchestrator.generate_preview_summary",
            new=AsyncMock(return_value=None),
        ):
            async for chunk in stream_agent_events(
                state,
                preview_lifecycle=True,
                execution_tables=tables,
            ):
                chunks.append(chunk)

        events = _parse_sse_events(chunks)
        sse_preview = next(
            (d for name, d in events if name == "preview_ready"), None
        )
        assert sse_preview is not None
        assert "previewHistory" in sse_preview
        assert len(sse_preview["previewHistory"]) == 1
        assert sse_preview["previewHistory"][0]["id"] == sse_preview["preview"]["id"]

        with patch(
            "app.agent.orchestrator.get_compiled_agent_graph",
            return_value=_mock_graph_returning_terminal(OutputPlanAction(payload=plan)),
        ):
            final_agent, action = await run_agent_orchestrated(
                state,
                preview_lifecycle=True,
                execution_tables=tables,
            )

        assert isinstance(action, PreviewReadyAction)
        sync_resp = _map_agent_result_to_response(final_agent, action)
        assert sync_resp["kind"] == "preview_ready"
        for key in ("plan", "preview", "previewHistory", "summary"):
            assert key in sync_resp
            assert key in sse_preview
        # p1-preview-summary-wire 方案 A：首包恒为 None，两条路径须一致。
        assert sync_resp["summary"] is None
        assert sse_preview["summary"] is None
        assert len(sync_resp["previewHistory"]) == len(sse_preview["previewHistory"]) == 1
        assert sync_resp["previewHistory"][0]["status"] == sse_preview["previewHistory"][0]["status"]
        assert sync_resp["preview"]["status"] == sse_preview["preview"]["status"] == "pending"
        assert sync_resp["preview"]["id"] == sync_resp["previewHistory"][0]["id"]
        assert sse_preview["preview"]["id"] == sse_preview["previewHistory"][0]["id"]

    asyncio.run(run())


def test_stream_and_sync_both_invoke_context_intent_analyzers() -> None:
    """SSE 与 sync 图入口均调用 ``analyze_context`` / ``analyze_intent``（同一函数）。"""

    async def run() -> None:
        state = _agent_state()
        plan = _good_plan()
        ctx_calls = 0
        intent_calls = 0

        def track_context(s: AgentState) -> AgentState:
            nonlocal ctx_calls
            ctx_calls += 1
            return s

        def track_intent(s: AgentState) -> AgentState:
            nonlocal intent_calls
            intent_calls += 1
            return s

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=plan)

        with patch("app.agent.orchestrator.analyze_context", side_effect=track_context), patch(
            "app.agent.orchestrator.analyze_intent", side_effect=track_intent
        ), patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ):
            chunks: list[str] = []
            async for chunk in stream_agent_events(state, preview_lifecycle=False):
                chunks.append(chunk)
            assert ctx_calls >= 1
            assert intent_calls >= 1
            sse_ctx, sse_intent = ctx_calls, intent_calls

        ctx_calls = 0
        intent_calls = 0
        with patch("app.agent.orchestrator.analyze_context", side_effect=track_context), patch(
            "app.agent.orchestrator.analyze_intent", side_effect=track_intent
        ), patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ):
            await run_agent_orchestrated(state, preview_lifecycle=False)

        assert ctx_calls >= 1
        assert intent_calls >= 1
        assert ctx_calls == sse_ctx
        assert intent_calls == sse_intent

    asyncio.run(run())


def test_sync_preview_ready_after_tool_loop() -> None:
    """Sync 路径：经 LangGraph tool 循环后返回 ``preview_ready``。"""

    async def run() -> None:
        state = _agent_state()
        tables = _tables()
        plan = _good_plan()
        step = 0

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            nonlocal step
            step += 1
            if step == 1:
                return (
                    s,
                    CallToolAction(
                        payload=CallToolPayload(
                            tool_name="get_table_schema",
                            tool_args={"table": "Sheet1"},
                        )
                    ),
                )
            return s, OutputPlanAction(payload=plan)

        with patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ):
            final_agent, action = await run_agent_orchestrated(
                state,
                preview_lifecycle=True,
                execution_tables=tables,
            )

        assert action_kind(action) == "preview_ready"
        assert isinstance(action, PreviewReadyAction)
        assert step == 2
        assert len(final_agent.preview_history) == 1

    asyncio.run(run())
