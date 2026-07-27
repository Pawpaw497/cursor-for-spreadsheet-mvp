"""pre_plan 子代理：确定性守护 + 表级裁剪 + 采集埋点。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-pre-plan-guards / p1-pre-plan-node /
p1-confidence-metrics-logging）。mock create_pa_agent，不打真实 LLM。
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.sub_agents import pre_plan
from app.models.agent_models import AgentState, TableContext
from app.models.plan import AgentRequestContext
from app.models.table_models import (
    ColumnProfile,
    DataContext,
    PrePlanContext,
    PrePlanSelection,
    TableProfile,
)


def _col(name: str = "price") -> ColumnProfile:
    return ColumnProfile(
        name=name,
        inferred_type="numeric",
        count=3,
        null_count=0,
        null_ratio=0.0,
        distinct_count=3,
    )


def _profile(name: str) -> TableProfile:
    return TableProfile(
        table_name=name, total_row_count=3, col_count=1, columns=[_col()]
    )


def _state(
    dc: DataContext | None,
    *,
    request_context: AgentRequestContext | None = None,
    messages: list[dict] | None = None,
) -> AgentState:
    tables = [
        TableContext(name=t.table_name, schema=[{"key": "price"}])
        for t in (dc.tables if dc else [])
    ]
    return AgentState(
        tables=tables,
        messages=messages if messages is not None else [],
        data_context=dc,
        request_context=request_context,
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


# ---- build_pre_plan_context ----


def test_single_table_skips_llm_call():
    dc = DataContext(tables=[_profile("t1")])
    state = _state(dc)
    with patch.object(pre_plan, "create_pa_agent") as mock_create:
        ctx = asyncio.run(pre_plan.build_pre_plan_context(state))
    mock_create.assert_not_called()
    assert ctx.selected_table_names == ["t1"]
    assert ctx.confidence == 1.0
    assert ctx.fallback_reason is None


def test_no_tables_skips_llm_call():
    state = _state(None)
    with patch.object(pre_plan, "create_pa_agent") as mock_create:
        ctx = asyncio.run(pre_plan.build_pre_plan_context(state))
    mock_create.assert_not_called()
    assert ctx.selected_table_names == []
    assert ctx.confidence == 1.0


def test_llm_selection_applied_for_multi_table():
    dc = DataContext(
        tables=[_profile("orders"), _profile("customers"), _profile("products")]
    )
    state = _state(dc)
    fake = _fake_agent(PrePlanSelection(selected_table_names=["orders"], confidence=0.9))
    with patch.object(pre_plan, "create_pa_agent", return_value=fake):
        ctx = asyncio.run(pre_plan.build_pre_plan_context(state))
    assert ctx.selected_table_names == ["orders"]
    assert ctx.confidence == 0.9
    assert ctx.fallback_reason is None


def test_active_table_guard_added_even_if_llm_omits_it():
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    ctx_req = AgentRequestContext(activeTable="customers")
    state = _state(dc, request_context=ctx_req)
    fake = _fake_agent(PrePlanSelection(selected_table_names=["orders"], confidence=0.9))
    with patch.object(pre_plan, "create_pa_agent", return_value=fake):
        ctx = asyncio.run(pre_plan.build_pre_plan_context(state))
    assert set(ctx.selected_table_names) == {"orders", "customers"}


def test_unknown_table_names_filtered_out():
    dc = DataContext(tables=[_profile("orders")])
    fake = _fake_agent(
        PrePlanSelection(selected_table_names=["orders", "ghost_table"], confidence=0.9)
    )
    with patch.object(pre_plan, "create_pa_agent", return_value=fake):
        ctx = asyncio.run(pre_plan.build_pre_plan_context(_state(dc)))
    assert ctx.selected_table_names == ["orders"]


def test_low_confidence_falls_back_to_all_tables():
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    fake = _fake_agent(PrePlanSelection(selected_table_names=["orders"], confidence=0.2))
    with patch.object(pre_plan, "create_pa_agent", return_value=fake):
        ctx = asyncio.run(pre_plan.build_pre_plan_context(_state(dc)))
    assert set(ctx.selected_table_names) == {"orders", "customers"}
    assert ctx.confidence == 0.2
    assert ctx.fallback_reason == "low_confidence"


def test_timeout_falls_back_to_all_tables():
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])

    async def _slow_run(*args, **kwargs):
        await asyncio.sleep(10)

    fake = AsyncMock()
    fake.run.side_effect = _slow_run
    with patch.object(pre_plan, "create_pa_agent", return_value=fake), patch.object(
        pre_plan, "PRE_PLAN_TIMEOUT_S", 0.01
    ):
        ctx = asyncio.run(pre_plan.build_pre_plan_context(_state(dc)))
    assert set(ctx.selected_table_names) == {"orders", "customers"}
    assert ctx.confidence == 0.0
    assert ctx.fallback_reason == "timeout"


def test_llm_error_falls_back_to_all_tables():
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    fake = _fake_agent(RuntimeError("boom"))
    with patch.object(pre_plan, "create_pa_agent", return_value=fake):
        ctx = asyncio.run(pre_plan.build_pre_plan_context(_state(dc)))
    assert set(ctx.selected_table_names) == {"orders", "customers"}
    assert ctx.confidence == 0.0
    assert ctx.fallback_reason == "error"


# ---- apply_pre_plan_pruning ----


def test_pruning_keeps_selected_table_columns_and_folds_others():
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    messages = [
        {"role": "user", "content": "Spreadsheet schema: ..."},
        {"role": "user", "content": "Data profile:\nstale"},
    ]
    state = _state(dc, messages=messages)
    ctx = PrePlanContext(selected_table_names=["orders"], confidence=0.9)
    new_state = pre_plan.apply_pre_plan_pruning(state, ctx)
    profile_msg = next(m for m in new_state.messages if m["content"].startswith("Data profile:"))
    assert "orders" in profile_msg["content"]
    assert "price:" in profile_msg["content"]
    assert "customers" in profile_msg["content"]
    # folded table: name present but no column-level detail line for it
    customers_block = profile_msg["content"].split("customers", 1)[1]
    assert "price:" not in customers_block.split("\n\n")[0]


def test_pruning_noop_when_all_tables_selected():
    dc = DataContext(tables=[_profile("orders")])
    messages = [
        {"role": "user", "content": "Spreadsheet schema: ..."},
        {"role": "user", "content": "Data profile:\nstale"},
    ]
    state = _state(dc, messages=messages)
    ctx = PrePlanContext(selected_table_names=["orders"], confidence=1.0)
    new_state = pre_plan.apply_pre_plan_pruning(state, ctx)
    profile_msg = next(m for m in new_state.messages if m["content"].startswith("Data profile:"))
    assert "price:" in profile_msg["content"]


# ---- restore_full_data_context ----


def test_restore_full_data_context_reverts_pruning_and_resets_ctx():
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    messages = [
        {"role": "user", "content": "Spreadsheet schema: ..."},
        {"role": "user", "content": "Data profile:\nstale"},
    ]
    state = _state(dc, messages=messages)
    pruned_ctx = PrePlanContext(selected_table_names=["orders"], confidence=0.9)
    pruned = pre_plan.apply_pre_plan_pruning(state, pruned_ctx).model_copy(
        update={"pre_plan_context": pruned_ctx}
    )
    restored = pre_plan.restore_full_data_context(pruned)
    profile_msg = next(m for m in restored.messages if m["content"].startswith("Data profile:"))
    assert "orders" in profile_msg["content"] and "customers" in profile_msg["content"]
    assert restored.pre_plan_context is not None
    assert set(restored.pre_plan_context.selected_table_names) == {"orders", "customers"}


# ---- run_pre_plan telemetry (p1-confidence-metrics-logging) ----


def test_run_pre_plan_logs_selection_metrics(caplog: pytest.LogCaptureFixture):
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    messages = [
        {"role": "user", "content": "Spreadsheet schema: ..."},
        {"role": "user", "content": "Data profile:\nstale"},
    ]
    state = _state(dc, messages=messages)
    fake = _fake_agent(PrePlanSelection(selected_table_names=["orders"], confidence=0.9))

    async def run():
        with caplog.at_level(logging.INFO):
            with patch.object(pre_plan, "create_pa_agent", return_value=fake):
                return await pre_plan.run_pre_plan(state)

    out = asyncio.run(run())
    record = next(r for r in caplog.records if r.message == "pre_plan_selection")
    assert record.confidence == 0.9
    assert record.fallback_reason is None
    assert record.selected_table_count == len(out.pre_plan_context.selected_table_names)
    assert record.elapsed_ms >= 0.0


def test_run_pre_plan_logs_fallback_reason_on_low_confidence(
    caplog: pytest.LogCaptureFixture,
):
    dc = DataContext(tables=[_profile("orders"), _profile("customers")])
    state = _state(dc)
    fake = _fake_agent(PrePlanSelection(selected_table_names=["orders"], confidence=0.1))

    async def run():
        with caplog.at_level(logging.INFO):
            with patch.object(pre_plan, "create_pa_agent", return_value=fake):
                return await pre_plan.run_pre_plan(state)

    asyncio.run(run())
    record = next(r for r in caplog.records if r.message == "pre_plan_selection")
    assert record.fallback_reason == "low_confidence"
    assert record.confidence == 0.1
    assert record.selected_table_count == 2


def test_run_pre_plan_logs_single_table_trivial_selection(
    caplog: pytest.LogCaptureFixture,
):
    dc = DataContext(tables=[_profile("t1")])
    state = _state(dc)

    async def run():
        with caplog.at_level(logging.INFO):
            return await pre_plan.run_pre_plan(state)

    asyncio.run(run())
    record = next(r for r in caplog.records if r.message == "pre_plan_selection")
    assert record.confidence == 1.0
    assert record.fallback_reason is None
    assert record.selected_table_count == 1
