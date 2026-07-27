"""Structured logs for clarify_scan ambiguity-scan outcomes.

见 .cursor/plans/pre-plan-clarify-scan-timeout-tuning.plan.md（add-clarify-scan-latency-telemetry）：
此前 clarify_scan 只有超时/异常分支打日志，成功路径完全没有埋点，导致无法区分
「真实跑完」与「命中超时墙」的延迟分布。字段对齐 pre_plan_telemetry.log_pre_plan_selection，
供后续阈值校准复用同一套日志解析逻辑。
"""
from __future__ import annotations

from app.logging_config import get_logger, get_trace_id

log = get_logger("agent.clarify_scan")


def log_clarify_scan_result(
    outcome: str,
    *,
    elapsed_ms: float,
    needs_clarification: bool,
    table_count: int,
) -> None:
    """Emit ``clarify_scan_result`` from every ``run_clarify_scan`` exit path.

    @param outcome: "skip"（0/1 张表或空 prompt）| "success"（真实跑完，未超时）|
        "timeout" | "error"。
    """
    log.info(
        "clarify_scan_result",
        extra={
            "trace_id": get_trace_id(),
            "outcome": outcome,
            "needs_clarification": needs_clarification,
            "table_count": table_count,
            "elapsed_ms": elapsed_ms,
        },
    )
