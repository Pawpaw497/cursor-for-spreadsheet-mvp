"""轮次累加的三条控制流性质——全部用 mock 驱动，不打真实 LLM。

计划里长期把「649s 不收敛」归因为「慢但成功的多轮累加」，并据此推导 `max_turns`
的取值。2026-08-14 复核发现：那个数来自已退役的 `gemini-2.5-flash-lite`，而该 case
本身是空 reason 的 ERROR；更要紧的是，被当作前提的三条控制流性质**从未被验证过**，
一直只有读代码得来的推断。本文件把它们变成可执行断言：

1. 失败轮次不累加——非 ``call_tool`` 的动作立即终止 run；
2. 成功轮次可以叠到 ``max_turns``，且触顶有明确的 finish 事件；
3. ``pa_decision_step`` 的递归自调用受 ``max_turns`` 约束（它在
   ``asyncio.timeout`` **之外**，每层另开一个完整窗口，但层数有界）。

这些是延迟合成表与 `max_turns` 取值的前提。数值标定另说——那要用 v4-pro 的实测
分布，不能用 mock，也不能再引用 gemini 的数据。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import patch

import pytest
from pydantic_ai.messages import ToolCallPart

from app.agent.actions import (
    CallToolAction,
    CallToolPayload,
    FinishAction,
    FinishPayload,
)
from app.agent.agent_helpers import state_after_turn
from app.agent.orchestrator import stream_agent_events
from app.agent.pa_decision import PaTurnResult, pa_decision_step
from app.models.agent_models import AgentState, TableContext


def _state(*, max_turns: int = 10) -> AgentState:
    return AgentState(
        tables=[TableContext(name="Sheet1", schema=[{"key": "a", "type": "string"}])],
        messages=[],
        user_prompt="test",
        model_source="cloud",
        max_turns=max_turns,
    )


def _events(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        m_event = re.search(r"event: (\w+)", chunk)
        m_data = re.search(r"data: (.+)", chunk)
        if m_event and m_data:
            out.append((m_event.group(1), json.loads(m_data.group(1))))
    return out


async def _drive(state: AgentState, step) -> tuple[list[tuple[str, dict]], int]:
    """跑完整个 stream，返回 (事件序, llm 决策被调用次数)。"""
    calls = {"n": 0}

    async def counting_step(s: AgentState, *, use_tools: bool = True):
        calls["n"] += 1
        return await step(s, calls["n"])

    chunks: list[str] = []
    with patch("app.agent.orchestrator.agent_react_step", side_effect=counting_step):
        async for chunk in stream_agent_events(state, preview_lifecycle=False):
            chunks.append(chunk)
    return _events(chunks), calls["n"]


def test_failed_turn_does_not_accumulate() -> None:
    """一次失败（非 ``call_tool``）立即终止 run——失败轮次不会叠加。

    这是「649s 是慢但成功的累加」这一说法的前提：如果失败轮次也能累加，那么一串
    超时就能自己堆出十几分钟，归因会完全不同。
    """

    async def step(s: AgentState, n: int):
        return s, FinishAction(FinishPayload(reason="llm_error: boom"))

    events, calls = asyncio.run(_drive(_state(max_turns=10), step))

    assert calls == 1, f"失败轮次不应重试或累加，实际调用 {calls} 次"
    assert [name for name, _ in events][-1] == "finish"


def test_successful_turns_accumulate_up_to_max_turns() -> None:
    """持续 ``call_tool`` 的成功轮次会一路叠到 ``max_turns``，并给出明确 finish。

    这条确认了天花板确实是 ``max_turns × 单轮上限``——即调低 ``max_turns`` 确实能
    压住累加，而不是压了个不存在的东西。
    """
    max_turns = 6
    calls = {"n": 0}

    async def always_tool_call(*_args: object, **_kwargs: object) -> PaTurnResult:
        calls["n"] += 1
        return PaTurnResult(
            tool_parts=[
                ToolCallPart(
                    tool_name="get_schema",
                    args={},
                    tool_call_id=f"c{calls['n']}",
                )
            ],
            text="",
            structured_plan=None,
        )

    async def run() -> list[tuple[str, dict]]:
        chunks: list[str] = []
        # 在 `_run_pa_single_turn` 这一层打桩，而不是 `agent_react_step`：轮次上限的
        # 唯一执行点在 pa_decision_step 内部（开头的 `current_turn >= max_turns` 早退），
        # 打在更外层等于把被测的东西一起 mock 掉。实测：从 agent_react_step 打桩时，
        # 即便桩自己 bump current_turn，图仍会在 llm_decide ↔ tool_exec 之间一直转到
        # langgraph 的 recursion_limit=25——见本文件末尾的说明用例。
        with patch(
            "app.agent.pa_decision._run_pa_single_turn", side_effect=always_tool_call
        ):
            async for chunk in stream_agent_events(
                _state(max_turns=max_turns), preview_lifecycle=False
            ):
                chunks.append(chunk)
        return _events(chunks)

    events = asyncio.run(run())

    # **一次工具往返消耗 2 个 turn**，不是 1：pa_decision_step 走 CallToolAction 时
    # `state_after_turn` 加一，随后 `run_tool_and_append_messages` 再加一
    # （agent_helpers.py:72）。于是 `max_turns` 实际允许的模型调用数是它的一半。
    # 这条关系此前没有任何文档或测试记录，却直接决定 `max_turns` 的语义。
    assert calls["n"] == max_turns // 2, (
        f"max_turns={max_turns} 应允许 {max_turns // 2} 次模型调用（每轮工具往返消耗 2 个 turn），"
        f"实际 {calls['n']}"
    )
    finish = [data for name, data in events if name == "finish"]
    assert finish and finish[-1]["reason"] == "max_turns"


def test_tool_round_costs_two_turns() -> None:
    """把「一次工具往返消耗 2 个 turn」这条关系单独钉死。

    两个 helper 各加一次：``state_after_turn``（决策产出 CallToolAction 时）与
    ``run_tool_and_append_messages``（工具执行后回灌 messages 时）。任何一边改了，
    ``max_turns`` 的实际含义就会静默变化——上限是 10 时，用户能得到的是 5 次模型
    调用而非 10 次。
    """
    from app.agent.agent_helpers import run_tool_and_append_messages

    base = _state(max_turns=10)
    after_decide = state_after_turn(base)
    assert after_decide.current_turn == base.current_turn + 1

    action = CallToolAction(
        payload=CallToolPayload(
            tool_name="get_schema", tool_args={}, tool_call_id="c1"
        )
    )
    after_tool = run_tool_and_append_messages(after_decide, action)
    assert after_tool.current_turn == after_decide.current_turn + 1


def test_pa_decision_recursion_is_bounded_by_max_turns() -> None:
    """递归自调用受 ``max_turns`` 约束——每层 bump ``current_turn``，撞早退。

    ``pa_decision_step`` 在工具参数非法时会带 feedback 递归自调用，而这些递归发生在
    ``async with asyncio.timeout(_PA_TURN_TIMEOUT_S)`` **之外**：每层另开一个完整的
    turn 窗口。所以单次用户可见的「一轮」在时间上可以叠若干个窗口——**但层数有界**，
    因为 ``state_with_user_feedback`` 内部会 bump ``current_turn``。

    这条此前只有读代码的推断。用一个永远返回非法参数的模型把递归逼到底，数层数。
    """
    max_turns = 3
    calls = {"n": 0}

    async def always_bad_args(*_args: object, **_kwargs: object) -> PaTurnResult:
        calls["n"] += 1
        return PaTurnResult(
            tool_parts=[
                ToolCallPart(
                    tool_name="peek_table",
                    args="not-a-json-object",
                    tool_call_id=f"c{calls['n']}",
                )
            ],
            text="",
            structured_plan=None,
        )

    async def run() -> None:
        with patch(
            "app.agent.pa_decision._run_pa_single_turn", side_effect=always_bad_args
        ):
            _, action = await pa_decision_step(_state(max_turns=max_turns), use_tools=True)
        assert isinstance(action, FinishAction)

    asyncio.run(run())

    assert calls["n"] <= max_turns, (
        f"递归应受 max_turns={max_turns} 约束，实际发起 {calls['n']} 次模型调用——"
        "若无界，单次用户可见轮次的耗时上限也就无界"
    )
    assert calls["n"] > 1, "本用例应真的触发递归，否则没测到东西"
