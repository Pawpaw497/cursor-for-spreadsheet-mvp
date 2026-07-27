"""PrePlanContext 契约：字段/边界校验。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-pre-plan-contract）。
仅测模型本身；裁剪/fallback 的行为逻辑属于后续 p1-pre-plan-node。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.table_models import PrePlanContext


def test_minimal_valid_context():
    ctx = PrePlanContext(selected_table_names=["销售订单"], confidence=0.9)
    assert ctx.selected_table_names == ["销售订单"]
    assert ctx.confidence == 0.9
    assert ctx.fallback_reason is None


def test_empty_selected_table_names_allowed():
    """守护规则（activeTable/selection 必保留）由 p1-pre-plan-guards 强制，不在本模型内校验。"""
    ctx = PrePlanContext(selected_table_names=[], confidence=0.0, fallback_reason="low_confidence")
    assert ctx.selected_table_names == []


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_confidence_boundary_values_valid(confidence: float):
    ctx = PrePlanContext(selected_table_names=["t"], confidence=confidence)
    assert ctx.confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, -1.0, 2.0])
def test_confidence_out_of_range_rejected(confidence: float):
    with pytest.raises(ValidationError):
        PrePlanContext(selected_table_names=["t"], confidence=confidence)


@pytest.mark.parametrize("reason", ["timeout", "low_confidence", "error"])
def test_fallback_reason_accepts_known_literals(reason: str):
    ctx = PrePlanContext(selected_table_names=["t"], confidence=0.2, fallback_reason=reason)
    assert ctx.fallback_reason == reason


def test_fallback_reason_rejects_unknown_literal():
    with pytest.raises(ValidationError):
        PrePlanContext(selected_table_names=["t"], confidence=0.2, fallback_reason="bogus")


def test_missing_required_fields_rejected():
    with pytest.raises(ValidationError):
        PrePlanContext(selected_table_names=["t"])  # confidence 缺失


def test_default_fallback_reason_is_none_without_explicit_value():
    ctx = PrePlanContext(selected_table_names=["a", "b"], confidence=1.0)
    assert ctx.fallback_reason is None
