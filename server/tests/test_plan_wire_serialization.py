"""Plan / PreviewRecord wire JSON must use public aliases (from, as) for frontend Zod."""
from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock, patch

from app.agent.actions import OutputPlanAction, PreviewReadyAction, PreviewReadyPayload
from app.agent.orchestrator import stream_agent_events
from app.agent.state import AgentState
from app.api.routes.agent import _map_agent_result_to_response
from app.models.agent_models import PreviewRecord, TableContext
from app.models.plan import Plan, plan_to_wire_dict, preview_record_to_wire_dict
from app.services.agent_preview import build_preview_record
from app.services.plan_executor import SchemaCol, TableData


def _multi_table_plan() -> Plan:
    return Plan.model_validate(
        {
            "intent": "lookup and aggregate",
            "steps": [
                {
                    "action": "lookup_column",
                    "mainTable": "销售订单",
                    "lookupTable": "产品信息",
                    "mainKey": "产品",
                    "lookupKey": "产品名称",
                    "columns": [
                        {"from": "类别", "to": "类别"},
                        {"from": "成本价", "to": "成本价"},
                    ],
                },
                {
                    "action": "aggregate_table",
                    "source": "销售订单",
                    "groupBy": ["客户"],
                    "resultTable": "客户毛利汇总",
                    "aggregations": [
                        {"column": "金额", "op": "sum", "as": "总金额"},
                        {"column": "毛利", "op": "sum", "as": "总毛利"},
                    ],
                },
            ],
        }
    )


def _assert_wire_plan_keys(plan_dict: dict[str, Any]) -> None:
    lookup = plan_dict["steps"][0]
    assert lookup["action"] == "lookup_column"
    col0 = lookup["columns"][0]
    assert "from" in col0
    assert "from_" not in col0

    agg_step = next(s for s in plan_dict["steps"] if s["action"] == "aggregate_table")
    agg0 = agg_step["aggregations"][0]
    assert "as" in agg0
    assert "as_" not in agg0


def test_plan_to_wire_dict_uses_aliases() -> None:
    wire = plan_to_wire_dict(_multi_table_plan())
    _assert_wire_plan_keys(wire)
    dumped = json.dumps(wire)
    assert '"from_' not in dumped
    assert '"as_' not in dumped


def test_build_preview_record_stores_wire_plan() -> None:
    plan = _multi_table_plan()
    rec = build_preview_record(
        plan=plan,
        diff={"addedColumns": [], "modifiedColumns": [], "validationWarnings": [], "validationErrors": []},
        new_tables=[],
        tables_fingerprint="fp",
    )
    _assert_wire_plan_keys(rec.plan)


def test_preview_record_to_wire_dict_normalizes_legacy_from_underscore() -> None:
    plan = _multi_table_plan()
    legacy = plan.model_dump()
    rec = PreviewRecord(
        id="p1",
        plan=legacy,
        diff={"addedColumns": [], "modifiedColumns": [], "validationWarnings": [], "validationErrors": []},
        created_at=0.0,
    )
    wire = preview_record_to_wire_dict(rec)
    _assert_wire_plan_keys(wire["plan"])


def test_map_agent_result_preview_ready_wire_aliases() -> None:
    plan = _multi_table_plan()
    preview = build_preview_record(
        plan=plan,
        diff={"addedColumns": [], "modifiedColumns": [], "validationWarnings": [], "validationErrors": []},
        new_tables=["客户毛利汇总"],
        tables_fingerprint="fp",
    )
    state = AgentState(
        tables=[
            TableContext(
                name="销售订单",
                schema=[{"key": "a", "type": "string"}],
            )
        ],
        messages=[],
        user_prompt="test",
        preview_history=[preview],
    )
    action = PreviewReadyAction(
        payload=PreviewReadyPayload(plan=plan, preview=preview),
    )
    resp = _map_agent_result_to_response(state, action)
    assert resp["kind"] == "preview_ready"
    _assert_wire_plan_keys(resp["plan"])
    _assert_wire_plan_keys(resp["preview"]["plan"])
    _assert_wire_plan_keys(resp["previewHistory"][0]["plan"])


def test_stream_agent_events_plan_done_wire_aliases() -> None:
    async def run() -> None:
        plan = _multi_table_plan()
        state = AgentState(
            tables=[
                TableContext(
                    name="Sheet1",
                    schema=[{"key": "a", "type": "string"}],
                )
            ],
            messages=[],
            user_prompt="test",
        )

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=plan)

        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.agent_react_step",
            new=mock_react_step,
        ):
            async for chunk in stream_agent_events(state, preview_lifecycle=False):
                chunks.append(chunk)

        events = []
        for chunk in chunks:
            m_event = re.search(r"event: (\w+)", chunk)
            m_data = re.search(r"data: (.+)", chunk)
            if m_event and m_data:
                events.append((m_event.group(1), json.loads(m_data.group(1))))

        plan_done = next((d for name, d in events if name == "plan_done"), None)
        assert plan_done is not None
        _assert_wire_plan_keys(plan_done["plan"])

    asyncio_run = __import__("asyncio").run
    asyncio_run(run())


def test_stream_agent_events_preview_ready_wire_aliases() -> None:
    async def run() -> None:
        plan = _multi_table_plan()
        state = AgentState(
            tables=[
                TableContext(
                    name="Sheet1",
                    schema=[{"key": "a", "type": "string"}],
                )
            ],
            messages=[],
            user_prompt="test",
        )
        tables = {
            "Sheet1": TableData(
                name="Sheet1",
                rows=[{"a": "x"}],
                schema=[SchemaCol(key="a", type="string")],
            )
        }

        async def mock_react_step(s: AgentState, *, use_tools: bool = True):
            return s, OutputPlanAction(payload=plan)

        chunks: list[str] = []
        with patch(
            "app.agent.orchestrator.agent_react_step",
            new=mock_react_step,
        ), patch(
            "app.agent.orchestrator.generate_preview_summary",
            new=AsyncMock(return_value=None),
        ):
            async for chunk in stream_agent_events(
                state, preview_lifecycle=True, execution_tables=tables
            ):
                chunks.append(chunk)

        events = []
        for chunk in chunks:
            m_event = re.search(r"event: (\w+)", chunk)
            m_data = re.search(r"data: (.+)", chunk)
            if m_event and m_data:
                events.append((m_event.group(1), json.loads(m_data.group(1))))

        preview_ev = next((d for name, d in events if name == "preview_ready"), None)
        assert preview_ev is not None
        _assert_wire_plan_keys(preview_ev["plan"])
        _assert_wire_plan_keys(preview_ev["preview"]["plan"])
        assert "previewHistory" in preview_ev
        assert len(preview_ev["previewHistory"]) == 1
        assert preview_ev["previewHistory"][0]["id"] == preview_ev["preview"]["id"]

    __import__("asyncio").run(run())
