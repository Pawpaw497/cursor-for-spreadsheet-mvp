"""Explicit Agent context package assembly (Memory Blueprint Stage 4)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent.memory_context import build_memory_context_block
from app.models.agent_models import AgentState, TableContext
from app.models.plan import AgentRequestContext
from app.models.table_models import ColumnProfile, DataContext, TableProfile

# 兼容 re-export：实现移至 message_discriminators，历史调用方与测试不破坏。
from app.agent.message_discriminators import (  # noqa: F401
    DATA_PROFILE_PREFIX,
    is_data_profile_message,
)


class AgentContextPackage(BaseModel):
    """Versioned context slices for one Agent LLM call."""

    tables: list[TableContext]
    selection: AgentRequestContext | None = None
    workspace_rules: str | None = None
    memory_block: str = ""
    transcript_summary: str = ""
    selection_context_text: str | None = None


def _summarize_transcript(messages: list[dict[str, Any]], *, max_turns: int = 6) -> str:
    """Compact reference to prior transcript turns (not full replay)."""
    if not messages:
        return ""
    lines: list[str] = []
    for m in messages[-max_turns:]:
        role = str(m.get("role") or "user")
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 120:
            content = content[:117] + "…"
        lines.append(f"- {role}: {content}")
    if not lines:
        return ""
    omitted = len(messages) - min(len(messages), max_turns)
    header = "Prior transcript (recent turns):"
    if omitted > 0:
        header = f"Prior transcript ({omitted} earlier turn(s) omitted, recent):"
    return header + "\n" + "\n".join(lines)


def build_selection_context_text(ctx: AgentRequestContext | None) -> str | None:
    """Human-readable selection + workspace rules snippet for prompt injection."""
    if ctx is None:
        return None

    parts: list[str] = []
    rules = (ctx.workspaceRules or "").strip()
    if rules:
        parts.append(f"Workspace rules:\n{rules}")

    selection_lines: list[str] = []
    if ctx.activeTable:
        selection_lines.append(f"Active table: {ctx.activeTable}")
    if ctx.focusedColumn:
        selection_lines.append(f"Focused column: {ctx.focusedColumn}")
    if ctx.selectedRange is not None:
        r = ctx.selectedRange
        start = r.startRow + 1
        end = r.endRow + 1
        if r.colIds:
            cols = ", ".join(r.colIds)
            selection_lines.append(
                f"Selected range on {ctx.activeTable or 'table'}: "
                f"rows {start}–{end}, columns {cols}"
            )
        else:
            selection_lines.append(
                f"Selected rows on {ctx.activeTable or 'table'}: {start}–{end}"
            )
    if selection_lines:
        parts.append("Current selection:\n" + "\n".join(f"- {line}" for line in selection_lines))

    if not parts:
        return None
    return "\n\n".join(parts)


def _format_number(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _render_column_line(col: ColumnProfile) -> str:
    head = f"- {col.name}: {col.inferred_type}"
    if col.off_type_count:
        head += f" ({col.off_type_count} off-type values)"
    parts = [head]
    parts.append(f"{col.null_ratio:.0%} null")
    parts.append(f"{col.distinct_count} distinct")
    if col.min_val is not None and col.max_val is not None:
        parts.append(f"range {col.min_val}–{col.max_val}")
    if col.mean is not None:
        parts.append(f"mean {_format_number(col.mean)}")
    if col.std is not None:
        parts.append(f"std {_format_number(col.std)}")
    if col.top_values:
        tv = ", ".join(f"{v} ({n})" for v, n in col.top_values)
        parts.append(f"top: {tv}")
    return ", ".join(parts)


def _render_intent_line(t: TableProfile) -> str | None:
    """topic/description/granularity 任一非空即渲染一行；三字段全无则返回 None。"""
    parts = []
    if t.topic:
        parts.append(f"topic: {t.topic}")
    if t.description:
        parts.append(f"description: {t.description}")
    if t.granularity:
        parts.append(f"granularity: {t.granularity}")
    if not parts:
        return None
    return "  " + " | ".join(parts)


def build_data_context_text(
    dc: DataContext | None, selected_table_names: list[str] | None = None
) -> str:
    """DataContext → 紧凑 prompt 文本；空则返回空串（调用方据此跳过注入）。

    @param selected_table_names: 非 None 时启用 pre_plan 表级裁剪渲染——命中的表
        全量渲染列详情，未命中的表折叠为单行（仅名称/行列数），不裁列（p1-pre-plan-guards）。
        None（默认）保持既有全量渲染行为，兼容 context_analyzer/intent_analyzer 现有调用。
    """
    if dc is None or not dc.tables:
        return ""
    selected = set(selected_table_names) if selected_table_names is not None else None
    blocks: list[str] = []
    for t in dc.tables:
        header = f'Table "{t.table_name}" ({t.total_row_count} rows, {t.col_count} columns)'
        if selected is not None and t.table_name not in selected:
            blocks.append(header + " — folded, not selected by pre_plan.")
            continue
        if t.profile_sampled:
            header += " (distinct/top values sampled)"
        lines = [header + ":"]
        intent_line = _render_intent_line(t)
        if intent_line:
            lines.append(intent_line)
        lines.extend(_render_column_line(c) for c in t.columns)
        blocks.append("\n".join(lines))
    return DATA_PROFILE_PREFIX + "\n\n".join(blocks)


def refresh_data_profile_message(
    state: AgentState, selected_table_names: list[str] | None = None
) -> AgentState:
    """用当前 ``state.data_context`` 重新渲染并原地替换 transcript 里的 Data profile 消息。

    intent_analyzer 在 context_analyzer 之后运行，只改 ``data_context`` 不够——
    ``context_analyzer`` 已经把渲染文本注入 ``messages``，不刷新的话 llm_decide
    读到的还是回填前的旧文本。找不到旧 profile 消息（context_analyzer 判定跳过
    注入的场景，如 transcript 无 schema 消息）时原样返回，不新增注入。

    @param selected_table_names: 见 ``build_data_context_text``；pre_plan 裁剪/还原时传入，
        intent_analyzer 的既有调用不传，保持全量渲染。
    """
    if state.data_context is None:
        return state
    text = build_data_context_text(state.data_context, selected_table_names)
    if not text:
        return state
    messages = list(state.messages)
    idx = next(
        (i for i, m in enumerate(messages) if is_data_profile_message(m)), None
    )
    if idx is None:
        return state
    messages[idx] = {"role": "user", "content": text}
    return state.model_copy(update={"messages": messages})


def selection_context_user_message(ctx: AgentRequestContext | None) -> dict[str, str] | None:
    """OpenAI-shaped user message for selection / workspace rules."""
    text = build_selection_context_text(ctx)
    if not text:
        return None
    return {"role": "user", "content": text}


def assemble_agent_context(
    state: AgentState,
    request_extras: AgentRequestContext | None = None,
) -> AgentContextPackage:
    """Build explicit context package for one Agent call."""
    ctx = request_extras or state.request_context
    rules = (ctx.workspaceRules or "").strip() if ctx else None
    return AgentContextPackage(
        tables=list(state.tables),
        selection=ctx,
        workspace_rules=rules or None,
        memory_block=build_memory_context_block(state),
        transcript_summary=_summarize_transcript(state.messages),
        selection_context_text=build_selection_context_text(ctx),
    )
