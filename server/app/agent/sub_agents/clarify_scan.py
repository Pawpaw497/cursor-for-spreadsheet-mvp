"""LLM 软扫描：Plan 生成前识别语义歧义（join 键不明、指代不清等）。

见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-clarify-dual-track）。

设计要点：
- 与规则轨（``clarification.py`` 的 ``maybe_need_clarification``，post-plan
  确定性 gate）时机不同：软扫描在 ``intent_analyzer`` 之后、``pre_plan`` 之前跑
  （见 ``orchestrator.build_agent_graph``），命中则直接返回
  ``AskClarificationAction``、不再进入 ``pre_plan``/``llm_decide``（省掉后续 LLM
  往返成本）；未命中则正常继续，仍可能被 post-plan 规则轨兜底命中——两轨职责不
  重叠：规则轨管「表/列缺失等逻辑硬伤」，软扫描管「语义模糊」。
- 同步阻塞，``CLARIFY_SCAN_TIMEOUT_S`` 超时或调用异常一律 fail-open（视为未命中，
  不阻塞主流程）——与 ``pre_plan`` 的 fail-safe（fallback 到全量 context）不同，
  软扫描没有「退回什么」的概念：宁可漏检也不能让软扫描故障挡住整个 Agent 流程。
- 0/1 张表时跳过 LLM 调用：本模块瞄准的语义歧义场景（join 键不明、跨表指代
  不清）本质上是多表问题，单表请求已有规则轨 + ``ask_user`` tool 兜底，不值得
  为此在每轮请求上都多付一次 LLM 往返（与 ``pre_plan`` 的裁剪跳过同一动机）。
- ``options`` 优先用 LLM 给出的候选；LLM 没给且存在多表时，退化为全部表名（与
  规则轨 ``options=table_names`` 的既有约定一致，便于 eval/前端复用同一渲染逻辑）。
"""
from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel
from pydantic_ai.settings import ModelSettings

from app.agent.actions import AskClarificationAction, ClarificationPayload
from app.agent.clarify_scan_telemetry import log_clarify_scan_result
from app.logging_config import get_logger
from app.models.agent_models import AgentState
from app.services.llm import OPENROUTER_HTTP_TIMEOUT_CHAT_S
from app.services.llm_pydantic_ai import create_pa_agent

logger = get_logger("agent.clarify_scan")

# Plan 生成前的快速软扫描，与 pre_plan 一样刻意压在同一量级的延迟预算内。
CLARIFY_SCAN_TIMEOUT_S = 0.8

_CLARIFY_SCAN_INSTRUCTIONS = (
    "You screen a user's spreadsheet request for semantic ambiguity before a plan "
    "is generated - e.g. an unclear join key between tables, an ambiguous pronoun "
    "or referent ('that column', 'the other table'), or a request that could "
    "reasonably mean two different things. Do NOT flag missing table names or "
    "structural gaps - those are caught separately by deterministic rules. Only "
    "flag genuine semantic ambiguity a person would need to resolve by answering a "
    "clarifying question. If uncertain, prefer NOT flagging - false positives "
    "interrupt the user unnecessarily. When you do flag, give concrete options if "
    "there are natural candidates (e.g. column names to pick a join key from)."
)


class ClarifyScanResult(BaseModel):
    """软扫描 LLM 调用的结构化输出（pydantic-ai result_type）。"""

    needs_clarification: bool = False
    question: str | None = None
    options: list[str] | None = None


async def scan_for_ambiguity(
    user_prompt: str,
    table_names: list[str],
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> ClarifyScanResult:
    """调用轻量模型扫描语义歧义；调用方负责超时/异常兜底，本函数不吞异常。"""
    agent = create_pa_agent(
        model_source,
        cloud_model_id=cloud_model_id,
        local_model_id=local_model_id,
        instructions=_CLARIFY_SCAN_INSTRUCTIONS,
        result_type=ClarifyScanResult,
    )
    prompt = (
        f"User request:\n{user_prompt}\n\nAvailable tables: "
        + (", ".join(table_names) if table_names else "(none)")
    )
    result = await agent.run(
        prompt, model_settings=ModelSettings(timeout=OPENROUTER_HTTP_TIMEOUT_CHAT_S)
    )
    return result.output


async def run_clarify_scan(state: AgentState) -> AskClarificationAction | None:
    """图节点主体：命中则返回 ``AskClarificationAction``，否则 ``None``（fail-open）。

    所有出口（skip/success/timeout/error）统一经 ``log_clarify_scan_result`` 打点，
    见 clarify_scan_telemetry.py（此前只有超时/异常分支有日志）。
    """
    t0 = time.perf_counter()
    prompt = (state.user_prompt or "").strip()
    table_names = [t.name for t in state.tables]

    def _log(outcome: str, *, needs_clarification: bool = False) -> None:
        log_clarify_scan_result(
            outcome,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            needs_clarification=needs_clarification,
            table_count=len(table_names),
        )

    if not prompt:
        _log("skip")
        return None

    if len(table_names) <= 1:
        _log("skip")
        return None

    try:
        scan = await asyncio.wait_for(
            scan_for_ambiguity(
                prompt,
                table_names,
                state.model_source,
                cloud_model_id=state.cloud_model_id,
                local_model_id=state.local_model_id,
            ),
            timeout=CLARIFY_SCAN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.info(
            "clarify_scan: timed out after %.2fs, fail-open", CLARIFY_SCAN_TIMEOUT_S
        )
        _log("timeout")
        return None
    except Exception:
        logger.warning("clarify_scan: scan failed, fail-open", exc_info=True)
        _log("error")
        return None

    if not scan.needs_clarification or not scan.question:
        _log("success", needs_clarification=scan.needs_clarification)
        return None

    options = scan.options or (table_names if len(table_names) > 1 else None)
    _log("success", needs_clarification=True)
    return AskClarificationAction(
        payload=ClarificationPayload(
            question=scan.question,
            options=options,
            context="Flagged by pre-plan semantic ambiguity scan.",
        )
    )
