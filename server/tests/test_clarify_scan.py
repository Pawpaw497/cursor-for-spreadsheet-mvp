"""clarify_scan 子代理：pre-llm_decide LLM 软扫描语义歧义。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-clarify-dual-track）。
mock create_pa_agent，不打真实 LLM。
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.actions import (
    AskClarificationAction,
    ClarificationPayload,
    FinishAction,
    FinishPayload,
    action_kind,
)
from app.agent.orchestrator import run_agent_orchestrated, stream_agent_events
from app.agent.sub_agents import clarify_scan as cs
from app.models.agent_models import AgentState, TableContext


def _state(
    *,
    user_prompt: str = "join orders and products",
    table_names: tuple[str, ...] = ("orders", "products"),
) -> AgentState:
    return AgentState(
        tables=[TableContext(name=n, schema=[{"key": "a"}]) for n in table_names],
        messages=[],
        user_prompt=user_prompt,
    )


class _FakeResult:
    def __init__(self, output):
        self.output = output


def _fake_agent(output):
    agent = AsyncMock()
    if isinstance(output, Exception):
        agent.run.side_effect = output
    else:
        agent.run.return_value = _FakeResult(output)
    return agent


# ---- run_clarify_scan ----


def test_empty_prompt_skips_llm_call():
    state = _state(user_prompt="   ")
    with patch.object(cs, "create_pa_agent") as mock_create:
        out = asyncio.run(cs.run_clarify_scan(state))
    mock_create.assert_not_called()
    assert out is None


def test_single_table_skips_llm_call():
    """语义歧义场景本质是多表问题，单表跳过，不多付一次 LLM 往返。"""
    state = _state(table_names=("Sheet1",))
    with patch.object(cs, "create_pa_agent") as mock_create:
        out = asyncio.run(cs.run_clarify_scan(state))
    mock_create.assert_not_called()
    assert out is None


def test_no_tables_skips_llm_call():
    state = _state(table_names=())
    with patch.object(cs, "create_pa_agent") as mock_create:
        out = asyncio.run(cs.run_clarify_scan(state))
    mock_create.assert_not_called()
    assert out is None


def test_not_ambiguous_returns_none():
    fake = _fake_agent(cs.ClarifyScanResult(needs_clarification=False))
    with patch.object(cs, "create_pa_agent", return_value=fake):
        out = asyncio.run(cs.run_clarify_scan(_state()))
    assert out is None


def test_ambiguous_hit_returns_ask_clarification_action():
    fake = _fake_agent(
        cs.ClarifyScanResult(
            needs_clarification=True,
            question="Which column should be used to join the two tables?",
            options=["产品", "产品名称"],
        )
    )
    with patch.object(cs, "create_pa_agent", return_value=fake):
        out = asyncio.run(cs.run_clarify_scan(_state()))
    assert isinstance(out, AskClarificationAction)
    assert "join" in out.payload.question.lower()
    assert out.payload.options == ["产品", "产品名称"]


def test_ambiguous_hit_without_llm_options_falls_back_to_table_names():
    fake = _fake_agent(
        cs.ClarifyScanResult(needs_clarification=True, question="Which table?")
    )
    with patch.object(cs, "create_pa_agent", return_value=fake):
        out = asyncio.run(cs.run_clarify_scan(_state(table_names=("orders", "products"))))
    assert isinstance(out, AskClarificationAction)
    assert out.payload.options == ["orders", "products"]


def test_hit_without_question_is_ignored():
    """needs_clarification=True 但没给出 question：视为无效命中，fail-open。"""
    fake = _fake_agent(cs.ClarifyScanResult(needs_clarification=True, question=None))
    with patch.object(cs, "create_pa_agent", return_value=fake):
        out = asyncio.run(cs.run_clarify_scan(_state()))
    assert out is None


def test_timeout_fails_open():
    async def _slow_run(*args, **kwargs):
        await asyncio.sleep(10)

    fake = AsyncMock()
    fake.run.side_effect = _slow_run
    with patch.object(cs, "create_pa_agent", return_value=fake), patch.object(
        cs, "CLARIFY_SCAN_TIMEOUT_S", 0.01
    ):
        out = asyncio.run(cs.run_clarify_scan(_state()))
    assert out is None


def test_llm_error_fails_open():
    fake = _fake_agent(RuntimeError("boom"))
    with patch.object(cs, "create_pa_agent", return_value=fake):
        out = asyncio.run(cs.run_clarify_scan(_state()))
    assert out is None


# ---- graph-level mutex: 软扫描命中时不进入 llm_decide/post-plan 规则轨 ----
# 见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-pre-plan-unit-tests）。


def _two_table_state() -> AgentState:
    return AgentState(
        tables=[
            TableContext(name="orders", schema=[{"key": "a"}]),
            TableContext(name="products", schema=[{"key": "b"}]),
        ],
        messages=[],
        user_prompt="join orders and products",
        max_turns=10,
    )


def _hit_action() -> AskClarificationAction:
    return AskClarificationAction(
        payload=ClarificationPayload(
            question="Which column should be used to join the two tables?",
            options=["orders", "products"],
        )
    )


def test_sync_clarify_scan_hit_short_circuits_before_llm_decide():
    async def run() -> None:
        state = _two_table_state()
        with patch(
            "app.agent.orchestrator.run_clarify_scan",
            new=AsyncMock(return_value=_hit_action()),
        ), patch(
            "app.agent.orchestrator.agent_react_step",
            new=AsyncMock(side_effect=AssertionError("llm_decide must not run")),
        ):
            _, action = await run_agent_orchestrated(state)
        assert action_kind(action) == "ask_clarification"
        assert action.payload.question == _hit_action().payload.question

    asyncio.run(run())


def test_sse_clarify_scan_hit_short_circuits_before_llm_decide():
    async def run() -> None:
        state = _two_table_state()
        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.run_clarify_scan",
            new=AsyncMock(return_value=_hit_action()),
        ), patch(
            "app.agent.orchestrator.agent_react_step",
            new=AsyncMock(side_effect=AssertionError("llm_decide must not run")),
        ):
            async for chunk in stream_agent_events(state):
                chunks.append(chunk)
        assert any('event: clarification' in c for c in chunks)
        assert not any('event: plan_done' in c for c in chunks)

    asyncio.run(run())


def test_clarify_scan_miss_reaches_llm_decide():
    """未命中（None）时正常继续到 llm_decide，不被误判为短路。"""

    async def run() -> None:
        state = _two_table_state()

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, FinishAction(FinishPayload(reason="done"))

        with patch(
            "app.agent.orchestrator.run_clarify_scan",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.agent.orchestrator.agent_react_step",
            new=AsyncMock(side_effect=mock_react_step),
        ) as m_react:
            _, action = await run_agent_orchestrated(state)
        assert action_kind(action) == "finish"
        m_react.assert_awaited_once()

    asyncio.run(run())


# ---- run_clarify_scan telemetry (add-clarify-scan-latency-telemetry) ----
# 见 .cursor/plans/pre-plan-clarify-scan-timeout-tuning.plan.md：clarify_scan 此前只有
# 超时/异常分支有日志，成功路径（未超时）完全没有埋点，本节补齐所有出口。


def test_empty_prompt_logs_skip(caplog: pytest.LogCaptureFixture):
    state = _state(user_prompt="   ")
    with caplog.at_level(logging.INFO):
        with patch.object(cs, "create_pa_agent") as mock_create:
            asyncio.run(cs.run_clarify_scan(state))
    mock_create.assert_not_called()
    record = next(r for r in caplog.records if r.message == "clarify_scan_result")
    assert record.outcome == "skip"
    assert record.needs_clarification is False
    assert record.table_count == 2
    assert record.elapsed_ms >= 0.0


def test_single_table_logs_skip(caplog: pytest.LogCaptureFixture):
    state = _state(table_names=("Sheet1",))
    with caplog.at_level(logging.INFO):
        asyncio.run(cs.run_clarify_scan(state))
    record = next(r for r in caplog.records if r.message == "clarify_scan_result")
    assert record.outcome == "skip"
    assert record.table_count == 1


def test_not_ambiguous_logs_success(caplog: pytest.LogCaptureFixture):
    fake = _fake_agent(cs.ClarifyScanResult(needs_clarification=False))
    with caplog.at_level(logging.INFO):
        with patch.object(cs, "create_pa_agent", return_value=fake):
            asyncio.run(cs.run_clarify_scan(_state()))
    record = next(r for r in caplog.records if r.message == "clarify_scan_result")
    assert record.outcome == "success"
    assert record.needs_clarification is False
    assert record.table_count == 2


def test_ambiguous_hit_logs_success_with_needs_clarification(
    caplog: pytest.LogCaptureFixture,
):
    fake = _fake_agent(
        cs.ClarifyScanResult(
            needs_clarification=True,
            question="Which column should be used to join the two tables?",
            options=["产品", "产品名称"],
        )
    )
    with caplog.at_level(logging.INFO):
        with patch.object(cs, "create_pa_agent", return_value=fake):
            asyncio.run(cs.run_clarify_scan(_state()))
    record = next(r for r in caplog.records if r.message == "clarify_scan_result")
    assert record.outcome == "success"
    assert record.needs_clarification is True


def test_timeout_logs_timeout_outcome(caplog: pytest.LogCaptureFixture):
    async def _slow_run(*args, **kwargs):
        await asyncio.sleep(10)

    fake = AsyncMock()
    fake.run.side_effect = _slow_run
    with caplog.at_level(logging.INFO):
        with patch.object(cs, "create_pa_agent", return_value=fake), patch.object(
            cs, "CLARIFY_SCAN_TIMEOUT_S", 0.01
        ):
            asyncio.run(cs.run_clarify_scan(_state()))
    record = next(r for r in caplog.records if r.message == "clarify_scan_result")
    assert record.outcome == "timeout"
    assert record.needs_clarification is False


def test_llm_error_logs_error_outcome(caplog: pytest.LogCaptureFixture):
    fake = _fake_agent(RuntimeError("boom"))
    with caplog.at_level(logging.INFO):
        with patch.object(cs, "create_pa_agent", return_value=fake):
            asyncio.run(cs.run_clarify_scan(_state()))
    record = next(r for r in caplog.records if r.message == "clarify_scan_result")
    assert record.outcome == "error"
    assert record.needs_clarification is False
