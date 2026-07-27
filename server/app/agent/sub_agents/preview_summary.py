"""Preview summary 子代理：把 Plan/Diff 转成用户可读的自然语言摘要。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-preview-summary-async）。

设计要点：
- 异步生成，不阻塞 ``preview_ready`` 基础响应（见 p1-preview-summary-wire 方案 A）——
  由 ``orchestrator.stream_agent_events`` 在 ``preview_ready``/``plan_done`` 发出后
  通过 ``asyncio.create_task`` 触发，SSE 流收尾前有限等待
  （``orchestrator.PREVIEW_SUMMARY_TIMEOUT_S``）；超时/异常一律降级为不发送
  ``preview_summary_ready``，不吞掉 SSE 流本身的异常/终止路径。
- 只读 ``PreviewRecord`` 的紧凑 Diff（addedColumns/modifiedColumns/newTables 等），
  不重新拉全表快照。
- 本模块不做重试/兜底——异常直接向上抛给调用方（``orchestrator``）决定降级策略，
  与 ``pre_plan.select_relevant_tables`` 的分工一致。
"""
from __future__ import annotations

from pydantic_ai.settings import ModelSettings

from app.models.agent_models import PreviewRecord
from app.services.llm import OPENROUTER_HTTP_TIMEOUT_CHAT_S
from app.services.llm_pydantic_ai import create_pa_agent

_PREVIEW_SUMMARY_INSTRUCTIONS = (
    "You summarize a spreadsheet plan's diff in one short, plain-language sentence "
    "for a user reviewing a preview before applying it. Mention the tables/columns "
    "actually affected. No markdown, do not mechanically restate raw field names."
)


def render_diff_for_summary(
    plan_intent: str, diff: dict[str, list[str]], new_tables: list[str]
) -> str:
    """``PreviewRecord`` 的紧凑 Diff → 摘要 LLM 的输入文本；无实质变更时返回空串。"""
    lines: list[str] = []
    if plan_intent:
        lines.append(f"Plan intent: {plan_intent}")
    if new_tables:
        lines.append("New tables: " + ", ".join(new_tables))
    if diff.get("addedColumns"):
        lines.append("Added columns: " + ", ".join(diff["addedColumns"]))
    if diff.get("modifiedColumns"):
        lines.append("Modified columns: " + ", ".join(diff["modifiedColumns"]))
    if diff.get("validationWarnings"):
        lines.append("Warnings: " + "; ".join(diff["validationWarnings"]))
    if diff.get("validationErrors"):
        lines.append("Errors: " + "; ".join(diff["validationErrors"]))
    return "\n".join(lines)


async def generate_preview_summary(
    record: PreviewRecord,
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> str | None:
    """生成一句话摘要；无实质 diff 或空 LLM 输出返回 ``None``（调用方据此跳过补发）。"""
    plan_intent = str((record.plan or {}).get("intent") or "")
    diff_text = render_diff_for_summary(plan_intent, record.diff, record.new_tables)
    if not diff_text:
        return None
    agent = create_pa_agent(
        model_source,
        cloud_model_id=cloud_model_id,
        local_model_id=local_model_id,
        instructions=_PREVIEW_SUMMARY_INSTRUCTIONS,
    )
    result = await agent.run(
        diff_text, model_settings=ModelSettings(timeout=OPENROUTER_HTTP_TIMEOUT_CHAT_S)
    )
    text = str(result.output or "").strip()
    return text or None
