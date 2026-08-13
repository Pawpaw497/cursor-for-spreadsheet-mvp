"""pa_decision 单轮时间预算：空串 ``llm_error:`` 的端到端复现与回归。

与 ``test_pa_decision.py`` 里既有的 timeout 用例不同——那条 patch 掉了
``_run_pa_single_turn`` 并直接 sleep，**没有走真实 HTTP 栈**，因此测不出
「SDK 重试把单轮拖过 ``_PA_TURN_TIMEOUT_S``」这个真实成因。本文件用
``MockTransport`` 模拟「上游挂起到 per-attempt 超时」，跑真实的
``pa_decision_step``，覆盖两条不同性质的性质：

* :func:`test_hung_upstream_yields_non_empty_reason` —— **硬保证**，与配置无关：
  无论重试怎么配、超时是否触发，用户拿到的 reason 都不该是空串。
* :func:`test_retry_budget_finishes_before_turn_deadline` —— **预算标定**，与配置强相关：
  按生产比例缩放后，重试预算须在外层 deadline 之前用完。

2026-08-14 实测（1/10 缩放，生产 90s/150s → 9s/15s）：
现状 ``max_retries=2`` + per-attempt 9s → 2 次尝试、击穿 15s、reason 为 ``'llm_error: '``；
目标 ``max_retries=1`` + per-attempt 6s → 12.52s 收尾、reason 非空。
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

import app.agent.pa_decision as pad
from app.agent.actions import FinishAction
from app.models.agent_models import AgentState, TableContext
from app.services import llm as llm_http
from app.services import llm_pydantic_ai as pa_llm

_REASON_PREFIX = "llm_error:"


def _state() -> AgentState:
    return AgentState(
        tables=[TableContext(name="Sheet1", schema=[{"key": "a", "type": "string"}])],
        messages=[],
        user_prompt="Add column",
        model_source="cloud",
        max_turns=10,
    )


def _patch_per_attempt(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    """把 pa_decision 的 per-attempt HTTP 超时改成 ``value``。

    PR1 会把这个值从共享的 ``OPENROUTER_HTTP_TIMEOUT_TOOLS_S`` 换成 PA 专用常量
    （共享常量还被非 PA 的 ``llm.py`` 用，直接改会连带改别处）。这里按名字探测，
    使本用例跨越那次重命名仍然有效。
    """
    for name in ("_PA_PER_ATTEMPT_TIMEOUT_S", "OPENROUTER_HTTP_TIMEOUT_TOOLS_S"):
        if hasattr(pad, name):
            monkeypatch.setattr(pad, name, value)
            return
    raise AssertionError("pa_decision 没有可识别的 per-attempt 超时常量")


async def _run_hung_turn(
    monkeypatch: pytest.MonkeyPatch, *, per_attempt: float, turn: float
) -> tuple[str, int, float]:
    """跑一轮对着「挂起到超时」的上游，返回 (reason, HTTP 尝试次数, 耗时秒)。"""
    attempts = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        await asyncio.sleep(per_attempt)
        raise httpx.ReadTimeout("simulated upstream hang", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm_http.set_shared_llm_http_client(client)
    monkeypatch.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(pad, "_PA_TURN_TIMEOUT_S", turn)
    _patch_per_attempt(monkeypatch, per_attempt)

    t0 = time.perf_counter()
    try:
        _, action = await pad.pa_decision_step(_state(), use_tools=True)
    finally:
        elapsed = time.perf_counter() - t0
        llm_http.set_shared_llm_http_client(None)
        await client.aclose()

    assert isinstance(action, FinishAction), f"期望 FinishAction，实际 {action!r}"
    assert action.payload is not None
    return action.payload.reason, attempts["n"], elapsed


def test_hung_upstream_yields_non_empty_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """挂起的上游必须产出**可诊断**的 reason，而不是 ``'llm_error: '``。

    这是本计划唯一与配置无关的硬保证：per-attempt / max_retries / turn 上限
    怎么调都不影响它。用极小的时间尺度，确保外层 ``asyncio.timeout`` 一定触发,
    正是裸 ``TimeoutError``（``str()`` 为空）逃逸的那条路径。
    """
    reason, _attempts, _elapsed = asyncio.run(
        _run_hung_turn(monkeypatch, per_attempt=0.05, turn=0.08)
    )

    assert reason.startswith(_REASON_PREFIX)
    detail = reason[len(_REASON_PREFIX) :].strip()
    assert detail, f"reason 去前缀后为空串（裸 TimeoutError 逃逸）：{reason!r}"


@pytest.mark.slow
def test_retry_budget_finishes_before_turn_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """按生产比例缩放后，重试预算须在外层 deadline **之前**用完。

    比例取生产目标值的 1/10（per-attempt 60s / turn 150s → 6s / 15s）。SDK 的重试
    退避是绝对秒数、不随缩放变小，1/10 下它的相对权重已接近生产；更激进的缩放会让
    退避喧宾夺主，反而测出错误结论（2026-08-14 用 1/60 缩放时就踩过这个坑）。

    击穿 deadline 本身不是 bug（上游可以慢到任何程度），但它意味着预算配错了：
    重试次数 × per-attempt 超过了单轮允许的时间，用户白等满一个 deadline。
    """
    _reason, attempts, elapsed = asyncio.run(
        _run_hung_turn(monkeypatch, per_attempt=6.0, turn=15.0)
    )

    assert elapsed < 15.0, (
        f"重试预算击穿了 turn deadline：{attempts} 次尝试耗时 {elapsed:.2f}s ≥ 15.0s。"
        " per-attempt 与 max_retries 的乘积须留出余量给 SDK 退避睡眠。"
    )
