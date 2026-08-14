"""验证 llm-fastfail-s1-v4pro 计划 Risk 表里两条标注「未验证」的假设。

两条假设各自的原文与本文件的验证方式：

1. **``max_retries=1`` 削弱瞬时 429 容错** —— 原文「有意取舍：PA 路径只此一层，
   置 0 更差」，其中「置 0 就一次 429 收场」至今是推测。
   → :func:`test_transient_429_recovers_with_current_max_retries` 与
   :func:`test_max_retries_zero_gives_up_on_first_429`（反事实对照）用同一个
   429→200 序列跑真实 openai SDK 重试层，把推测变成观测；
   :func:`test_two_consecutive_429s_exhaust_current_budget` 标出该容错的**上界**。

2. **内外两层输出重试是否等价** —— Risk 表担心内层 ``AGENT_RETRIES['output']``
   收紧会削弱结构化输出修复，因为「外层 ``MAX_PLAN_VALIDATION_RETRIES`` 承担同一
   职责」。→ :func:`test_inner_and_outer_output_retries_send_different_second_request`
   直接比对两层各自发出的**第二次请求体**，
   :func:`test_two_output_retry_layers_are_reachable_on_disjoint_paths` 比对二者的
   可达路径。结论是两层既不等价也不互为备份。

所有 fixture 遵循 ``test_llm_malformed_response.py`` 的约束：模拟上游响应必须显式
带 ``content-type: application/json``，否则会走到与真实 OpenRouter 不同的分支。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.settings import ModelSettings

import app.agent.pa_decision as pad
from app.models.agent_models import AgentState, TableContext
from app.models.table_models import TableIntentBatch
from app.services import llm as llm_http
from app.services import llm_pydantic_ai as pa_llm

JSON_CT = {"content-type": "application/json"}

PROMPT = 'Table "orders" (10 rows):\n- id: int, 0% null, 10 distinct'

_VALID_INTENT_BATCH = {
    "tables": [
        {
            "table_name": "orders",
            "topic": "orders",
            "description": "one row per order",
            "granularity": "one row per order",
        }
    ]
}


@pytest.fixture(autouse=True)
def _reset_shared_llm_client():
    llm_http.set_shared_llm_http_client(None)
    yield
    llm_http.set_shared_llm_http_client(None)


def _chat_completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "test/model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _tool_call_completion(tool_name: str, args: dict) -> dict:
    """带 tool_call 的 chat.completion——``final_result`` 即结构化输出的载体。"""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "test/model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _subagent_tool_call_completion(args: dict) -> dict:
    """子 agent 的**生产形态**响应：走 ``final_result`` tool call。

    我们对结构化输出一律发 ``tools=[final_result]`` + ``tool_choice=required``
    （见 :func:`test_request_shape_is_identical_across_model_ids`），因此上游正常
    情况下回的是 tool call，而不是文本 JSON。
    """
    return _tool_call_completion("final_result", args)


def _ok_intent_batch(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json=_chat_completion(json.dumps(_VALID_INTENT_BATCH)),
        headers=JSON_CT,
        request=request,
    )


def _too_many_requests(request: httpx.Request) -> httpx.Response:
    """瞬时限流：不带 ``Retry-After``，让 SDK 走自己的退避（约 0.5s）。

    带 ``Retry-After`` 会把上游的睡眠时长写进用例，测的就不再是我们的预算了。
    """
    return httpx.Response(
        429,
        json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
        headers=JSON_CT,
        request=request,
    )


def _run_subagent(
    responses: list[Callable[[httpx.Request], httpx.Response]],
    *,
    max_retries: int | None = None,
    cloud_model_id: str = "test/model",
) -> tuple[object, int, list[dict[str, Any]]]:
    """用 ``agent.run()`` 跑一次结构化输出调用（子 agent 路径）。

    ``responses`` 按序消费，耗尽后重复最后一个。返回
    ``(结果或异常, HTTP 请求次数, 各次请求的 JSON body)``。
    """
    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        idx = min(len(bodies), len(responses) - 1)
        bodies.append(json.loads(request.content))
        return responses[idx](request)

    async def run() -> object:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm_http.set_shared_llm_http_client(client)
        try:
            agent = pa_llm.create_pa_agent(
                "cloud",
                cloud_model_id=cloud_model_id,
                instructions="classify",
                result_type=TableIntentBatch,
            )
            try:
                result = await agent.run(PROMPT, model_settings=ModelSettings(timeout=10.0))
                return result.output
            except Exception as e:  # noqa: BLE001 — 用例要断言异常本身
                return e
        finally:
            llm_http.set_shared_llm_http_client(None)
            await client.aclose()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
        if max_retries is not None:
            mp.setattr(pa_llm, "SDK_MAX_RETRIES", max_retries)
        outcome = asyncio.run(run())
    return outcome, len(bodies), bodies


# --------------------------------------------------------------------------
# 假设 1：max_retries=1 与瞬时 429
# --------------------------------------------------------------------------


def test_transient_429_recovers_with_current_max_retries() -> None:
    """一次瞬时 429 之后上游恢复 → 当前预算能自愈，调用方看不到错误。

    这是 Risk 表「有意取舍」那一格的**正面证据**：``SDK_MAX_RETRIES=1`` 仍保住了
    单次限流抖动的容错，不是把重试整个关掉。
    """
    assert pa_llm.SDK_MAX_RETRIES >= 1, "本用例的前提是 SDK 层还留着至少一次重试"

    outcome, calls, _ = _run_subagent(
        [
            _too_many_requests,
            lambda req: httpx.Response(
                200,
                json=_chat_completion(json.dumps(_VALID_INTENT_BATCH)),
                headers=JSON_CT,
                request=req,
            ),
        ]
    )

    assert isinstance(outcome, TableIntentBatch), f"未自愈，实际：{outcome!r}"
    assert outcome.tables[0].table_name == "orders"
    assert calls == 2, "应为「首次 429 + 重试成功」两次请求"


def test_max_retries_zero_gives_up_on_first_429() -> None:
    """反事实对照：``max_retries=0`` 下同一序列在第一次 429 就收场。

    计划里「置 0 等于一次瞬时 429 就 FinishAction 收场」原本是推测，本用例把它
    变成观测——两个用例喂的是**同一个** 429→200 序列，唯一变量是重试预算。
    """
    outcome, calls, _ = _run_subagent(
        [
            _too_many_requests,
            lambda req: httpx.Response(
                200,
                json=_chat_completion(json.dumps(_VALID_INTENT_BATCH)),
                headers=JSON_CT,
                request=req,
            ),
        ],
        max_retries=0,
    )

    assert isinstance(outcome, ModelHTTPError), f"期望 429 逃逸，实际：{outcome!r}"
    assert outcome.status_code == 429
    assert calls == 1, "置 0 时第二个（成功的）响应根本没机会被请求"


def test_two_consecutive_429s_exhaust_current_budget() -> None:
    """当前预算的**上界**：连续两次 429 就顶不住了。

    记下这条是为了不把上一个用例的结论读成「429 容错已解决」。它同时说明
    ``max_retries`` 只覆盖**瞬时**抖动；持续限流属于另一类问题，加大重试次数只会
    把单轮拖过 ``_PA_TURN_TIMEOUT_S``（见 test_pa_decision_timeout_budget.py）。
    """
    outcome, calls, _ = _run_subagent(
        [
            _too_many_requests,
            _too_many_requests,
            lambda req: httpx.Response(
                200,
                json=_chat_completion(json.dumps(_VALID_INTENT_BATCH)),
                headers=JSON_CT,
                request=req,
            ),
        ]
    )

    assert isinstance(outcome, ModelHTTPError)
    assert outcome.status_code == 429
    assert calls == pa_llm.SDK_MAX_RETRIES + 1


# --------------------------------------------------------------------------
# 假设 2：内外两层输出重试是否等价
# --------------------------------------------------------------------------


def _bad_plan_args(null_count: int) -> dict:
    """通不过 ``Plan`` 校验的 ``final_result`` 参数（steps 里塞 null）。

    ``null_count`` 让两次错误文本不同，绕开 ``pa_decision`` 的
    「同一错误不重试」短路，从而观察到真正的第二次请求。
    """
    return {"intent": "add x", "steps": [None] * null_count}


def _run_pa_decision_against(
    responses: list[Callable[[httpx.Request], httpx.Response]],
) -> tuple[object, list[dict[str, Any]]]:
    """跑真实 ``pa_decision_step``（外层路径），返回 (action, 各次请求 body)。"""
    bodies: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        idx = min(len(bodies), len(responses) - 1)
        bodies.append(json.loads(request.content))
        return responses[idx](request)

    state = AgentState(
        tables=[TableContext(name="Sheet1", schema=[{"key": "a", "type": "string"}])],
        messages=[],
        user_prompt="add a column named x",
        model_source="cloud",
        max_turns=10,
    )

    async def run() -> object:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm_http.set_shared_llm_http_client(client)
        try:
            _, action = await pad.pa_decision_step(state, use_tools=False)
            return action
        finally:
            llm_http.set_shared_llm_http_client(None)
            await client.aclose()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
        action = asyncio.run(run())
    return action, bodies


def _roles(body: dict[str, Any]) -> list[str]:
    return [m.get("role") for m in body.get("messages", [])]


def test_inner_and_outer_output_retries_send_different_second_request() -> None:
    """两层重试发出的**第二次请求**形态不同——故不等价、也不互为备份。

    **不能靠消息角色区分**：内层追加消息的角色随上游**怎么回**而变（回 tool call
    → ``tool``；回文本 JSON → ``user``），由
    :func:`test_inner_retry_role_follows_upstream_reply_shape` 单独钉住。2026-08-14
    首版用例假定内层恒为 ``tool``、外层恒为 ``user``，被打红。

    跨这两种上游形态都成立的差异有两处：

    1. **历史**：内层保留失败的那条 assistant 消息（模型看得见自己刚才写错的输出）；
       外层是重开一轮，请求里根本没有 assistant 消息。
    2. **反馈内容**：内层回灌的是 pydantic 的原始校验错误 dump（``validation
       error`` + ``Fix the errors and try again``）；外层送的是我们自己
       ``_summarize_plan_validation_error`` 写的领域反馈（点名 ``final_result`` /
       ``steps`` 并给出正确示例）。

    即「同一次对话内带原始报错重发」 vs 「带人工归纳反馈重开一轮」。收紧内层不会由
    外层补上，反之亦然。
    """
    # 内层：子 agent 路径，final_result 参数不合法 → pydantic-ai 自己重试
    inner_outcome, inner_calls, inner_bodies = _run_subagent(
        [
            lambda req: httpx.Response(
                200,
                json=_subagent_tool_call_completion({"tables": "not-a-list"}),
                headers=JSON_CT,
                request=req,
            )
        ]
    )
    assert inner_calls == 2, f"内层未重试（实际 {inner_calls} 次）：{inner_outcome!r}"

    # 外层：pa_decision 路径，final_result 通不过 Plan 校验 → pa_decision_step 递归
    outer_action, outer_bodies = _run_pa_decision_against(
        [
            lambda req: httpx.Response(
                200,
                json=_tool_call_completion("final_result", _bad_plan_args(5)),
                headers=JSON_CT,
                request=req,
            ),
            lambda req: httpx.Response(
                200,
                json=_tool_call_completion("final_result", _bad_plan_args(4)),
                headers=JSON_CT,
                request=req,
            ),
        ]
    )
    assert len(outer_bodies) >= 2, f"外层未重试：{outer_action!r}"

    # 差异 1：历史。内层保留失败的 assistant 输出，外层不保留。
    assert "assistant" in _roles(inner_bodies[1]), (
        f"内层应保留失败的 assistant 消息，实际 {_roles(inner_bodies[1])}"
    )
    assert "assistant" not in _roles(outer_bodies[1]), (
        f"外层是重开一轮，不应带上一轮的 assistant 消息，实际 {_roles(outer_bodies[1])}"
    )

    # 差异 2：反馈内容。内层是 pydantic 原始 dump，外层是我们写的领域反馈。
    inner_last = inner_bodies[1]["messages"][-1]["content"]
    outer_last = outer_bodies[1]["messages"][-1]["content"]
    assert "validation error" in inner_last, f"内层反馈非原始校验 dump：{inner_last!r}"
    assert "final_result" in outer_last and "steps" in outer_last, (
        f"外层未送出 _summarize_plan_validation_error 的领域反馈：{outer_last!r}"
    )
    assert inner_last != outer_last


def test_two_output_retry_layers_are_reachable_on_disjoint_paths() -> None:
    """两层的可达路径不相交——这才是「不等价」的根本原因。

    * ``pa_decision`` 用 ``agent.iter()`` 且在第一个 CallToolsNode 处 break，内层
      output retry 够不到（既有用例
      ``test_llm_pydantic_ai.py::test_agent_layer_retries_unreachable_on_iter_break_path``
      已锁住），那里**只有**外层。
    * 四个子 agent 用 ``agent.run()``，那里**只有**内层，没有任何外层递归兜底。

    故 Risk 表「外层承担同一职责」的措辞不成立：收紧内层直接削弱子 agent，没有
    任何东西替它兜底。
    """
    # pa_decision 路径：一次坏 final_result 只发一次请求（内层没介入），
    # 第二次请求来自外层递归。
    _action, outer_bodies = _run_pa_decision_against(
        [
            lambda req: httpx.Response(
                200,
                json=_tool_call_completion("final_result", _bad_plan_args(5)),
                headers=JSON_CT,
                request=req,
            ),
            lambda req: httpx.Response(
                200,
                json=_tool_call_completion("final_result", _bad_plan_args(4)),
                headers=JSON_CT,
                request=req,
            ),
        ]
    )
    # 外层预算 MAX_PLAN_VALIDATION_RETRIES 决定总轮数；若内层也生效，请求数会翻倍。
    assert len(outer_bodies) <= pad.MAX_PLAN_VALIDATION_RETRIES + 1, (
        f"请求数 {len(outer_bodies)} 超过外层预算，说明内层也在这条路径上生效了"
    )

    # 子 agent 路径：没有任何外层，重试次数完全由内层决定。
    _outcome, inner_calls, _ = _run_subagent(
        [
            lambda req: httpx.Response(
                200,
                json=_subagent_tool_call_completion({"tables": "not-a-list"}),
                headers=JSON_CT,
                request=req,
            )
        ]
    )
    assert inner_calls == pa_llm.AGENT_RETRIES["output"] + 1, (
        "子 agent 的总请求数应恰好等于内层预算 + 1——没有外层参与"
    )


# --------------------------------------------------------------------------
# 上面两组结论对「换模型 / 换 provider 形态」的敏感性
# --------------------------------------------------------------------------

_MODEL_IDS = [
    "test/model",  # 各 fixture 用的占位 id
    "deepseek/deepseek-v4-pro",  # 当前生产模型（PR #49 锁定）
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash-lite",
    "anthropic/claude-sonnet-4",
]


def test_request_shape_is_identical_across_model_ids() -> None:
    """结构化输出的**出站请求形态**不随模型 id 变——这是各 fixture 用占位 id 的依据。

    pydantic-ai 按模型 profile 决定结构化输出走 tool call 还是 ``response_format``；
    若占位 id ``test/model`` 拿到的 profile 与生产模型不同，前面所有用例测的就是
    另一条分支。本用例把「不同」这件事直接观测掉：五个模型 id（含生产模型）发出的
    请求都是 ``tools=[final_result]`` + ``tool_choice=required``，无 ``response_format``。

    这条一旦变红，说明上游改了 profile 策略，本文件其余结论需要按新形态重测。
    """
    shapes = []
    for model_id in _MODEL_IDS:
        _outcome, _calls, bodies = _run_subagent(
            [
                lambda req: httpx.Response(
                    200,
                    json=_subagent_tool_call_completion(_VALID_INTENT_BATCH),
                    headers=JSON_CT,
                    request=req,
                )
            ],
            cloud_model_id=model_id,
        )
        body = bodies[0]
        shapes.append(
            (
                model_id,
                tuple(t["function"]["name"] for t in body.get("tools", ())),
                body.get("tool_choice"),
                body.get("response_format"),
            )
        )

    for model_id, tools, tool_choice, response_format in shapes:
        assert tools == ("final_result",), f"{model_id} 的 tools 形态不同：{tools}"
        assert tool_choice == "required", f"{model_id} 的 tool_choice 不同：{tool_choice}"
        assert response_format is None, f"{model_id} 走了 response_format：{response_format}"


def test_inner_retry_role_follows_upstream_reply_shape() -> None:
    """内层重试消息的**角色**随上游回复形态而变，不是常量。

    钉住这条是因为它是唯一一处「看起来像框架常量、实际是运行时变量」的地方：
    上游回 tool call → 重试写成 ``tool`` 角色（带 ``tool_call_id``）；回文本 JSON →
    写成 ``user`` 角色。两者的**内容**都是同一份 pydantic 校验 dump。

    由 :func:`test_request_shape_is_identical_across_model_ids` 可知我们总是发
    ``tool_choice=required``，故 ``tool`` 才是生产形态；``user`` 那条是上游不遵守
    tool_choice 时的退化路径。
    """
    _o1, _c1, tool_reply = _run_subagent(
        [
            lambda req: httpx.Response(
                200,
                json=_subagent_tool_call_completion({"tables": "not-a-list"}),
                headers=JSON_CT,
                request=req,
            )
        ]
    )
    _o2, _c2, text_reply = _run_subagent(
        [
            lambda req: httpx.Response(
                200,
                json=_chat_completion(json.dumps({"tables": "not-a-list"})),
                headers=JSON_CT,
                request=req,
            )
        ]
    )

    assert _roles(tool_reply[1])[-1] == "tool"
    assert _roles(text_reply[1])[-1] == "user"
    # 角色不同，但都携带同一份原始校验 dump——这才是可依赖的不变量。
    for bodies in (tool_reply, text_reply):
        assert "validation error" in bodies[1]["messages"][-1]["content"]


def test_429_tolerance_is_keyed_on_transport_status_not_on_model() -> None:
    """429 容错的触发条件是**传输层状态码**，与模型无关；但对 provider 形态敏感。

    同一个「先失败一次、再成功」的序列，只换第一次失败的**投递形态**：

    * HTTP 429 / HTTP 500 → SDK 重试，自愈（``Retry-After: 0`` 也一样自愈）；
    * **HTTP 200 + body 里的 error envelope → 一次都不重试**。

    最后一条是真实约束而非假想：``test_llm_malformed_response.py`` 已记录
    OpenRouter 会用 200 包错误。因此「``max_retries=1`` 扛得住瞬时 429」这句话的
    完整形式是——**只在上游把限流投递成 HTTP 429 时成立**；投递成 200 包时，
    ``max_retries`` 取任何值都没有重试。
    """
    retried_shapes = {
        "http_429": lambda req: httpx.Response(
            429, json={"error": {"message": "rate limited"}}, headers=JSON_CT, request=req
        ),
        "http_500": lambda req: httpx.Response(
            500, json={"error": {"message": "boom"}}, headers=JSON_CT, request=req
        ),
        "http_429_retry_after_0": lambda req: httpx.Response(
            429,
            json={"error": {"message": "rate limited"}},
            headers={**JSON_CT, "retry-after": "0"},
            request=req,
        ),
    }
    for name, failure in retried_shapes.items():
        outcome, calls, _ = _run_subagent([failure, _ok_intent_batch])
        assert isinstance(outcome, TableIntentBatch), f"{name} 未自愈：{outcome!r}"
        assert calls == 2, f"{name} 的请求数应为 2，实际 {calls}"

    # 200 包错误：SDK 看不到失败状态码，重试层根本不介入。
    outcome, calls, _ = _run_subagent(
        [
            lambda req: httpx.Response(
                200,
                json={"error": {"code": 429, "message": "rate limited"}},
                headers=JSON_CT,
                request=req,
            ),
            _ok_intent_batch,
        ]
    )
    assert calls == 1, (
        f"200 包的限流不该触发任何重试（实际 {calls} 次）；"
        "若这里变成 2，说明上游开始解析 body 里的 error，本文件的 429 结论需重述"
    )
    assert not isinstance(outcome, TableIntentBatch)
