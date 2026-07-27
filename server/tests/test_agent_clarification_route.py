"""POST /api/agent maps ask_clarification to clarification JSON (no real LLM)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agent import initial_state_from_agent_project_request
from app.agent.actions import AskClarificationAction, ClarificationPayload
from app.main import app
from app.models.plan import AgentProjectPlanRequest


@pytest.fixture()
def client() -> TestClient:
    """提供同步 HTTP 客户端。"""
    return TestClient(app)


def _two_table_agent_body() -> dict:
    table = {
        "schema": [{"key": "a", "type": "string"}],
        "sampleRows": [{"a": "x"}],
    }
    return {
        "prompt": "add a column named x",
        "tables": [
            {"name": "Sheet1", **table},
            {"name": "Sheet2", **table},
        ],
        "history": [],
        "previewLifecycle": False,
    }


def test_agent_returns_clarification_kind(client: TestClient) -> None:
    """_map_agent_result_to_response exposes kind=clarification without plan."""
    body = _two_table_agent_body()
    req = AgentProjectPlanRequest.model_validate(body)
    state = initial_state_from_agent_project_request(req)
    question = "Which table should receive the new column?"
    action = AskClarificationAction(
        payload=ClarificationPayload(
            question=question,
            options=["Sheet1", "Sheet2"],
            context="Ambiguous steps: add_column missing table",
        )
    )

    async def mock_run(*_args, **_kwargs):
        return state, action

    with patch(
        "app.api.routes.agent.run_agent_orchestrated",
        new=AsyncMock(side_effect=mock_run),
    ):
        resp = client.post("/api/agent", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "clarification"
    assert data.get("plan") is None
    assert data["clarification"]["question"] == question
    assert data["clarification"]["options"] == ["Sheet1", "Sheet2"]
    ctx = data["clarification"].get("context")
    assert ctx
    assert "Ambiguous" in ctx


def test_gate_fires_when_plan_omits_table(client: TestClient) -> None:
    """真实 orchestrator + gate：多表下 LLM 产出缺 table 的 write step → 澄清。

    锁定确定性澄清兜底（eval 侧 ambiguous case 已放宽为「澄清或显式合法 table」，
    此处保证 gate 路径本身不回归）。
    """
    from app.agent.pa_decision import PaTurnResult
    from app.models.plan import Plan

    plan = Plan.model_validate(
        {
            "intent": "add x",
            "steps": [{"action": "add_column", "name": "x", "expression": "1"}],
        }
    )
    turn = PaTurnResult(tool_parts=[], text="", structured_plan=plan)

    with patch(
        "app.agent.pa_decision._run_pa_single_turn",
        new=AsyncMock(return_value=turn),
    ), patch(
        "app.agent.orchestrator.run_clarify_scan",
        new=AsyncMock(return_value=None),
    ):
        resp = client.post("/api/agent", json=_two_table_agent_body())

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "clarification"
    assert data.get("plan") is None
    assert set(data["clarification"]["options"]) == {"Sheet1", "Sheet2"}
