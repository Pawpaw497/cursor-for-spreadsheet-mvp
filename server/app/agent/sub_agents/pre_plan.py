"""Pre-plan 子代理：Plan 生成前对 DataContext 做表级裁剪。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-pre-plan-guards / p1-pre-plan-node）。

设计要点：
- 只做**表级**裁剪，不裁列：选中表的 schema/profile 全量保留，未选中表折叠为
  单行索引（``context_assembler.build_data_context_text`` 的 ``selected_table_names``
  参数）。需要具体行数据一律走 ``peek_range`` 工具，不在本模块处理。
- 0/1 张表时裁剪无意义，跳过 LLM 调用直接全选。
- confidence < 0.5、超时（``PRE_PLAN_TIMEOUT_S``，同步阻塞）、LLM 异常三种情况
  一律 fallback 到全量表选择；``confidence``/``fallback_reason`` 保留真实值供
  p1-confidence-metrics-logging 采集分布，不做静默改写。
- ``activeTable``（用户当前选中表）是确定性守护：无论 LLM 输出什么都强制并入
  选中集合。
"""
from __future__ import annotations

import asyncio
import time

from pydantic_ai.settings import ModelSettings

from app.agent.context_assembler import refresh_data_profile_message
from app.agent.pre_plan_telemetry import log_pre_plan_selection
from app.logging_config import get_logger
from app.models.agent_models import AgentState
from app.models.table_models import DataContext, PrePlanContext, PrePlanSelection
from app.services.llm import OPENROUTER_HTTP_TIMEOUT_CHAT_S
from app.services.llm_pydantic_ai import create_pa_agent

logger = get_logger("agent.pre_plan")

# P1 内同步阻塞：超过此值直接 fallback 到全量 Context，不做 fire-and-forget 异步
# （异步化留给 P2 aux_llm）。
PRE_PLAN_TIMEOUT_S = 0.8

PRE_PLAN_CONFIDENCE_THRESHOLD = 0.5

_PRE_PLAN_INSTRUCTIONS = (
    "You select which spreadsheet tables are relevant to the user's request, given "
    "a short index of all available tables (name + inferred topic/description). "
    "Return the table names that are actually needed to answer the prompt, plus a "
    "confidence score (0-1) for your selection. If you are unsure which tables are "
    "relevant, or the prompt seems to touch most/all tables, return a low confidence "
    "score rather than guessing — a low score triggers a safe fallback to all tables."
)


def _all_table_names(dc: DataContext | None) -> list[str]:
    return [t.table_name for t in dc.tables] if dc else []


def render_table_index_for_pre_plan(dc: DataContext) -> str:
    """DataContext → 精简表索引（名称 + topic/description），供裁剪 LLM 选表用。

    刻意不含列级统计——裁剪阶段只需要知道"这张表大概是什么"，完整 profile
    仍会在渲染阶段为选中表保留（见 ``context_assembler.build_data_context_text``）。
    """
    lines = []
    for t in dc.tables:
        line = f'- "{t.table_name}" ({t.total_row_count} rows, {t.col_count} columns)'
        extras = [p for p in (t.topic, t.description) if p]
        if extras:
            line += ": " + "; ".join(extras)
        lines.append(line)
    return "\n".join(lines)


def _active_table_name(state: AgentState) -> str | None:
    ctx = state.request_context
    return getattr(ctx, "activeTable", None) if ctx is not None else None


def _guarded_names(
    selected: list[str], all_names: list[str], active_table: str | None
) -> list[str]:
    """过滤 LLM 输出到已知表名集合，并强制并入 activeTable（确定性守护）。"""
    known = set(all_names)
    guarded = [name for name in selected if name in known]
    if active_table and active_table in known and active_table not in guarded:
        guarded.append(active_table)
    return guarded


async def select_relevant_tables(
    dc: DataContext,
    user_prompt: str,
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> PrePlanSelection:
    """调用轻量模型选表；调用方负责超时/异常兜底，本函数不吞异常。"""
    agent = create_pa_agent(
        model_source,
        cloud_model_id=cloud_model_id,
        local_model_id=local_model_id,
        instructions=_PRE_PLAN_INSTRUCTIONS,
        result_type=PrePlanSelection,
    )
    prompt = (
        f"User request:\n{user_prompt}\n\nAvailable tables:\n"
        f"{render_table_index_for_pre_plan(dc)}"
    )
    result = await agent.run(
        prompt, model_settings=ModelSettings(timeout=OPENROUTER_HTTP_TIMEOUT_CHAT_S)
    )
    return result.output


async def build_pre_plan_context(state: AgentState) -> PrePlanContext:
    """主入口：产出本轮的 ``PrePlanContext``（不改写 state.messages，见 ``apply_pre_plan_pruning``）。"""
    dc = state.data_context
    all_names = _all_table_names(dc)

    if dc is None or len(all_names) <= 1:
        return PrePlanContext(selected_table_names=all_names, confidence=1.0)

    active_table = _active_table_name(state)

    try:
        selection = await asyncio.wait_for(
            select_relevant_tables(
                dc,
                state.user_prompt,
                state.model_source,
                cloud_model_id=state.cloud_model_id,
                local_model_id=state.local_model_id,
            ),
            timeout=PRE_PLAN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("pre_plan: selection timed out after %.2fs, fallback to all tables", PRE_PLAN_TIMEOUT_S)
        return PrePlanContext(
            selected_table_names=all_names, confidence=0.0, fallback_reason="timeout"
        )
    except Exception:
        logger.warning("pre_plan: selection failed, fallback to all tables", exc_info=True)
        return PrePlanContext(
            selected_table_names=all_names, confidence=0.0, fallback_reason="error"
        )

    if selection.confidence < PRE_PLAN_CONFIDENCE_THRESHOLD:
        logger.info(
            "pre_plan: low confidence %.2f < %.2f, fallback to all tables",
            selection.confidence,
            PRE_PLAN_CONFIDENCE_THRESHOLD,
        )
        return PrePlanContext(
            selected_table_names=all_names,
            confidence=selection.confidence,
            fallback_reason="low_confidence",
        )

    guarded = _guarded_names(selection.selected_table_names, all_names, active_table)
    return PrePlanContext(selected_table_names=guarded, confidence=selection.confidence)


def apply_pre_plan_pruning(state: AgentState, ctx: PrePlanContext) -> AgentState:
    """按 ``ctx.selected_table_names`` 重渲染 Data profile 消息（表级裁剪，见模块 docstring）。"""
    return refresh_data_profile_message(state, ctx.selected_table_names)


def restore_full_data_context(state: AgentState) -> AgentState:
    """回退到全量 Context：用于下游 Plan 校验失败后的一次性重试（p1-pre-plan-guards）。

    重新渲染全量 Data profile，并把 ``state.pre_plan_context`` 重置为"全选"，
    这样调用方（``orchestrator.agent_react_step``）据此判断裁剪已解除，
    不会对同一轮重复触发回退重试。
    """
    all_names = _all_table_names(state.data_context)
    restored = refresh_data_profile_message(state)
    full_ctx = PrePlanContext(selected_table_names=all_names, confidence=1.0)
    return restored.model_copy(update={"pre_plan_context": full_ctx})


async def run_pre_plan(state: AgentState) -> AgentState:
    """图节点主体：产出 ``PrePlanContext``、裁剪 Data profile 消息，并记录采集埋点。"""
    t0 = time.perf_counter()
    ctx = await build_pre_plan_context(state)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    log_pre_plan_selection(ctx, elapsed_ms=elapsed_ms)
    pruned = apply_pre_plan_pruning(state, ctx)
    return pruned.model_copy(update={"pre_plan_context": ctx})
