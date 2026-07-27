"""preview_summary 子代理：Diff → 自然语言摘要。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-preview-summary-async）。
mock create_pa_agent，不打真实 LLM。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.agent.sub_agents import preview_summary as ps
from app.models.agent_models import PreviewRecord


def _record(
    *,
    diff: dict[str, list[str]] | None = None,
    new_tables: list[str] | None = None,
    intent: str = "add a column",
) -> PreviewRecord:
    return PreviewRecord(
        id="pv1",
        plan={"intent": intent, "steps": []},
        diff=diff
        or {
            "addedColumns": ["x"],
            "modifiedColumns": [],
            "validationWarnings": [],
            "validationErrors": [],
        },
        new_tables=new_tables or [],
        status="pending",
        created_at=1.0,
    )


class _FakeResult:
    def __init__(self, output: str):
        self.output = output


def _fake_agent(output):
    agent = AsyncMock()
    if isinstance(output, Exception):
        agent.run.side_effect = output
    else:
        agent.run.return_value = _FakeResult(output)
    return agent


# ---- render_diff_for_summary ----


def test_render_diff_empty_when_nothing_changed():
    text = ps.render_diff_for_summary("", {}, [])
    assert text == ""


def test_render_diff_includes_intent_and_added_columns():
    text = ps.render_diff_for_summary(
        "add revenue column", {"addedColumns": ["revenue"]}, []
    )
    assert "add revenue column" in text
    assert "revenue" in text


def test_render_diff_includes_new_tables_and_errors():
    text = ps.render_diff_for_summary(
        "",
        {"validationErrors": ["boom"]},
        ["summary_table"],
    )
    assert "summary_table" in text
    assert "boom" in text


# ---- generate_preview_summary ----


def test_empty_diff_skips_llm_call():
    record = _record(
        diff={
            "addedColumns": [],
            "modifiedColumns": [],
            "validationWarnings": [],
            "validationErrors": [],
        },
        intent="",
    )
    with patch.object(ps, "create_pa_agent") as mock_create:
        out = asyncio.run(ps.generate_preview_summary(record, "cloud"))
    mock_create.assert_not_called()
    assert out is None


def test_returns_stripped_llm_text():
    record = _record()
    fake = _fake_agent("  Added a revenue column.  ")
    with patch.object(ps, "create_pa_agent", return_value=fake):
        out = asyncio.run(ps.generate_preview_summary(record, "cloud"))
    assert out == "Added a revenue column."


def test_blank_llm_output_returns_none():
    record = _record()
    fake = _fake_agent("   ")
    with patch.object(ps, "create_pa_agent", return_value=fake):
        out = asyncio.run(ps.generate_preview_summary(record, "cloud"))
    assert out is None


def test_llm_error_propagates_to_caller():
    record = _record()
    fake = _fake_agent(RuntimeError("boom"))
    with patch.object(ps, "create_pa_agent", return_value=fake):
        try:
            asyncio.run(ps.generate_preview_summary(record, "cloud"))
        except RuntimeError as e:
            assert str(e) == "boom"
        else:
            raise AssertionError("expected RuntimeError to propagate")
