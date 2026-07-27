"""同步与 SSE 编排器在 preview_lifecycle 下的预览修订行为。"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.actions import OutputPlanAction, PreviewReadyAction, action_kind
from app.agent.orchestrator import run_agent_orchestrated, stream_agent_events
from app.models.agent_models import AgentState, TableContext
from app.models.plan import Plan
from app.services.agent_preview import (
    MAX_AGENT_PREVIEW_REVISIONS,
    PreviewEvaluationReady,
    PreviewEvaluationRevise,
    build_preview_record,
    evaluate_output_plan_preview,
    fingerprint_execution_tables,
)
from app.services.plan_executor import SchemaCol, TableData


def _tables() -> dict[str, TableData]:
    return {
        "Sheet1": TableData(
            name="Sheet1",
            rows=[{"a": "x"}],
            schema=[SchemaCol(key="a", type="string")],
        )
    }


def _agent_state(*, revision_count: int = 0) -> AgentState:
    return AgentState(
        tables=[
            TableContext(
                name="Sheet1",
                schema=[{"key": "a", "type": "string"}],
            )
        ],
        messages=[],
        user_prompt="add column",
        revision_count=revision_count,
        max_turns=10,
    )


def _bad_plan() -> Plan:
    """error 级 validate_table 失败，触发自动修订。"""
    return Plan.model_validate(
        {
            "intent": "bad",
            "steps": [
                {
                    "action": "validate_table",
                    "rules": ["false"],
                    "level": "error",
                }
            ],
        }
    )


def _good_plan() -> Plan:
    return Plan.model_validate(
        {
            "intent": "ok",
            "steps": [
                {"action": "add_column", "name": "x", "expression": "1"},
            ],
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


def test_evaluate_output_plan_preview_revise_then_ready() -> None:
    agent = _agent_state()
    first = evaluate_output_plan_preview(agent, _bad_plan(), _tables())
    assert isinstance(first, PreviewEvaluationRevise)
    assert first.working_agent.revision_count == 1
    assert any("preview" in (m.get("content") or "").lower() for m in first.working_agent.messages)

    second = evaluate_output_plan_preview(
        first.working_agent, _good_plan(), _tables()
    )
    assert isinstance(second, PreviewEvaluationReady)
    assert len(second.agent.preview_history) == 1


def test_evaluate_output_plan_preview_cap_at_revision_limit() -> None:
    agent = _agent_state(revision_count=MAX_AGENT_PREVIEW_REVISIONS)
    result = evaluate_output_plan_preview(agent, _bad_plan(), _tables())
    assert isinstance(result, PreviewEvaluationReady)
    assert result.warnings
    assert any("revision limit" in w.lower() for w in result.warnings)
    assert result.record.execution_error is not None
    assert len(result.agent.preview_history) == 1


def test_evaluate_output_plan_preview_cap_reuses_pending_preview() -> None:
    tables = _tables()
    fp = fingerprint_execution_tables(tables)
    pending = build_preview_record(
        plan=_good_plan(),
        diff={
            "addedColumns": ["x"],
            "modifiedColumns": [],
            "validationWarnings": [],
            "validationErrors": [],
        },
        new_tables=[],
        tables_fingerprint=fp,
    )
    agent = _agent_state(revision_count=MAX_AGENT_PREVIEW_REVISIONS).model_copy(
        update={"preview_history": [pending]}
    )
    result = evaluate_output_plan_preview(agent, _bad_plan(), tables)
    assert isinstance(result, PreviewEvaluationReady)
    assert result.record.id == pending.id
    assert result.warnings
    assert len(result.agent.preview_history) == 1


def test_stream_agent_events_preview_retries_then_ready() -> None:
    async def run() -> None:
        state = _agent_state()
        tables = _tables()
        call_count = 0

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            nonlocal call_count
            call_count += 1
            plan = _bad_plan() if call_count == 1 else _good_plan()
            return (s, OutputPlanAction(payload=plan))

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
        event_names = [name for name, _ in events]
        assert "preview_ready" in event_names
        assert "plan_done" in event_names
        finish_reasons = [
            data["reason"] for name, data in events if name == "finish"
        ]
        assert not any(r.startswith("preview_dry_run_error") for r in finish_reasons)
        assert not any(
            r.startswith("preview_validation_error") for r in finish_reasons
        )
        assert call_count == 2

    asyncio.run(run())


def test_stream_agent_events_preview_cap_degraded_ready() -> None:
    async def run() -> None:
        state = _agent_state(revision_count=MAX_AGENT_PREVIEW_REVISIONS)
        tables = _tables()

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return (s, OutputPlanAction(payload=_bad_plan()))

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
        event_names = [name for name, _ in events]
        assert "preview_ready" in event_names
        preview_events = [data for name, data in events if name == "preview_ready"]
        assert preview_events[0].get("warnings")
        finishes = [name for name, _ in events if name == "finish"]
        assert not finishes

    asyncio.run(run())


def test_stream_agent_events_emits_preview_summary_ready_when_generated() -> None:
    """p1-preview-summary-async：摘要生成成功时，plan_done 后补发 preview_summary_ready。"""

    async def run() -> None:
        state = _agent_state()
        tables = _tables()

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=_good_plan())

        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ), patch(
            "app.agent.orchestrator.generate_preview_summary",
            new=AsyncMock(return_value="Added column x."),
        ):
            async for chunk in stream_agent_events(
                state, preview_lifecycle=True, execution_tables=tables
            ):
                chunks.append(chunk)

        events = _parse_sse_events(chunks)
        names = [n for n, _ in events]
        assert names[-3:] == ["preview_ready", "plan_done", "preview_summary_ready"]
        summary_ev = next(d for n, d in events if n == "preview_summary_ready")
        assert summary_ev["summary"] == "Added column x."
        preview_ev = next(d for n, d in events if n == "preview_ready")
        assert summary_ev["previewId"] == preview_ev["preview"]["id"]

    asyncio.run(run())


def test_stream_agent_events_skips_preview_summary_ready_on_timeout() -> None:
    """超时（>PREVIEW_SUMMARY_TIMEOUT_S）：不补发事件，plan_done 仍是流的终点。"""

    async def run() -> None:
        state = _agent_state()
        tables = _tables()

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=_good_plan())

        async def _slow_summary(*args, **kwargs):
            await asyncio.sleep(10)
            return "too late"

        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ), patch(
            "app.agent.orchestrator.generate_preview_summary",
            new=_slow_summary,
        ), patch("app.agent.orchestrator.PREVIEW_SUMMARY_TIMEOUT_S", 0.01):
            async for chunk in stream_agent_events(
                state, preview_lifecycle=True, execution_tables=tables
            ):
                chunks.append(chunk)

        events = _parse_sse_events(chunks)
        names = [n for n, _ in events]
        assert names[-1] == "plan_done"
        assert "preview_summary_ready" not in names

    asyncio.run(run())


def test_stream_agent_events_skips_preview_summary_ready_on_error() -> None:
    """摘要生成异常：不补发事件、不影响已发出的 preview_ready/plan_done。"""

    async def run() -> None:
        state = _agent_state()
        tables = _tables()

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=_good_plan())

        async def _boom(*args, **kwargs):
            raise RuntimeError("summary boom")

        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.agent_react_step",
            side_effect=mock_react_step,
        ), patch(
            "app.agent.orchestrator.generate_preview_summary",
            new=_boom,
        ):
            async for chunk in stream_agent_events(
                state, preview_lifecycle=True, execution_tables=tables
            ):
                chunks.append(chunk)

        events = _parse_sse_events(chunks)
        names = [n for n, _ in events]
        assert names[-1] == "plan_done"
        assert "preview_summary_ready" not in names

    asyncio.run(run())


def test_stream_agent_events_calls_pa_decision_step() -> None:
    """SSE loop invokes pa_decision_step via agent_react_step."""
    state = _agent_state()
    tables = _tables()
    pa_calls = 0

    async def mock_pa(s: AgentState, *, use_tools: bool = True):
        nonlocal pa_calls
        pa_calls += 1
        plan = _bad_plan() if pa_calls == 1 else _good_plan()
        return (s, OutputPlanAction(payload=plan))

    async def run() -> None:
        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.pa_decision_step",
            side_effect=mock_pa,
        ) as m_pa, patch(
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
        event_names = [name for name, _ in events]
        assert "preview_ready" in event_names
        assert "plan_done" in event_names
        assert pa_calls == 2
        assert m_pa.await_count == 2

    asyncio.run(run())


def test_run_agent_orchestrated_calls_pa_decision_step() -> None:
    """Graph llm_decide invokes pa_decision_step via agent_react_step."""
    state = _agent_state()
    tables = _tables()
    pa_calls = 0

    async def mock_pa(s: AgentState, *, use_tools: bool = True):
        nonlocal pa_calls
        pa_calls += 1
        plan = _bad_plan() if pa_calls == 1 else _good_plan()
        return (s, OutputPlanAction(payload=plan))

    async def run() -> None:
        with patch(
            "app.agent.orchestrator.pa_decision_step",
            side_effect=mock_pa,
        ) as m_pa:
            final_agent, action = await run_agent_orchestrated(
                state,
                preview_lifecycle=True,
                execution_tables=tables,
            )
        assert action_kind(action) == "preview_ready"
        assert pa_calls == 2
        assert m_pa.await_count == 2
        assert len(final_agent.preview_history) == 1

    asyncio.run(run())


def test_run_agent_orchestrated_preview_cap_degraded_ready() -> None:
    state = _agent_state(revision_count=MAX_AGENT_PREVIEW_REVISIONS)
    tables = _tables()
    compiled = AsyncMock()

    async def fake_ainvoke(init):
        agent_dump = init["agent"]
        return {
            "agent": agent_dump,
            "scratch": {
                "ser_action": {
                    "kind": "output_plan",
                    "plan": _bad_plan().model_dump(),
                }
            },
        }

    compiled.ainvoke = fake_ainvoke

    async def run() -> None:
        with patch(
            "app.agent.orchestrator.get_compiled_agent_graph",
            return_value=compiled,
        ):
            final_agent, action = await run_agent_orchestrated(
                state,
                preview_lifecycle=True,
                execution_tables=tables,
            )
        assert action_kind(action) == "preview_ready"
        assert isinstance(action, PreviewReadyAction)
        assert action.payload.warnings
        assert len(final_agent.preview_history) == 1

    asyncio.run(run())


def test_run_agent_orchestrated_preview_retries_then_ready() -> None:
    state = _agent_state()
    tables = _tables()
    call_count = 0
    compiled = AsyncMock()

    async def fake_ainvoke(init):
        nonlocal call_count
        call_count += 1
        agent_dump = init["agent"]
        plan = _bad_plan() if call_count == 1 else _good_plan()
        return {
            "agent": agent_dump,
            "scratch": {
                "ser_action": {
                    "kind": "output_plan",
                    "plan": plan.model_dump(),
                }
            },
        }

    compiled.ainvoke = fake_ainvoke

    async def run() -> None:
        with patch(
            "app.agent.orchestrator.get_compiled_agent_graph",
            return_value=compiled,
        ):
            final_agent, action = await run_agent_orchestrated(
                state,
                preview_lifecycle=True,
                execution_tables=tables,
            )
        assert action_kind(action) == "preview_ready"
        assert isinstance(action, PreviewReadyAction)
        assert call_count == 2
        assert len(final_agent.preview_history) == 1

    asyncio.run(run())
