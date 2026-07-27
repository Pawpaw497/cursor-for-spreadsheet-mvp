"""Structured logs for pre_plan table-selection outcomes.

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-confidence-metrics-logging）：
仅采集 confidence 分布供 Phase 2 YOLO 快慢路径阈值校准，不作 P1 交付门槛。
"""
from __future__ import annotations

from app.logging_config import get_logger, get_trace_id
from app.models.table_models import PrePlanContext

log = get_logger("agent.pre_plan")


def log_pre_plan_selection(ctx: PrePlanContext, *, elapsed_ms: float) -> None:
    """Emit ``pre_plan_selection`` with the fields Phase 2 needs for threshold calibration."""
    log.info(
        "pre_plan_selection",
        extra={
            "trace_id": get_trace_id(),
            "confidence": ctx.confidence,
            "fallback_reason": ctx.fallback_reason,
            "selected_table_count": len(ctx.selected_table_names),
            "elapsed_ms": elapsed_ms,
        },
    )
