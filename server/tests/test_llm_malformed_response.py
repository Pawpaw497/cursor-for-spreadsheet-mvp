"""上游畸形响应与重试放大在真实响应路径上的行为锁定（HTTP 200 但 body 不可用）。

背景与根因确认见 https://github.com/Pawpaw497/cursor-for-spreadsheet/pull/30
（_SafeOpenAIChatModel + asyncio.timeout 修复 finish_reason='error' 挂起）。

**mock 保真度是本文件的核心约束**（2026-07-28 教训）：首版用例的 malformed fixture
**漏了 content-type 头**，导致 openai SDK 走「非 JSON content-type → 返回原始文本」
分支，异常被 pydantic-ai 包装成 UnexpectedModelBehavior——与真实 OpenRouter（永远
发 ``application/json``）行为完全不同，测试全绿却锁死了错误结论。因此：

- 所有模拟上游响应的 fixture **必须显式带 content-type**；
- ``test_content_type_governs_which_failure_path`` 专门把这个陷阱本身钉住。

锁定的结论：
1. ``content-type: application/json`` + 非法 body → 裸 ``json.JSONDecodeError`` 逃逸
   出 ``agent.run()``；它是 ``ValueError`` 子类；
2. 该失败**不重试**（1 次请求）；但**传输层超时/连接中断**、HTTP 5xx/429 都会被
   openai SDK 按 ``SDK_MAX_RETRIES`` 重试，输出校验失败再由 Agent 层重试 1 次
   （两层互相独立，不要用一个推另一个）。其中**传输层超时那条**才是
   ``_PA_TURN_TIMEOUT_S`` 被击穿、产生空串 reason 的真实机制（eval 日志里并无
   5xx 记录）；端到端复现见 ``test_pa_decision_timeout_budget.py``；
3. ``finish_reason='error'`` 由 pydantic 的 ChatCompletion 校验拦下，
   ``_SafeOpenAIChatModel._map_finish_reason`` 真实路径上不被调用；
4. HTTP 200 + 合法 JSON + 输出结构「可选字段缺失」→ 不报错不重试，静默空结果。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.settings import ModelSettings

from app.models.table_models import TableIntentBatch
from app.services import llm as llm_http
from app.services import llm_pydantic_ai as pa_llm

PROMPT = 'Table "orders" (10 rows):\n- id: int, 0% null, 10 distinct'

#: 真实 OpenRouter 响应恒带此头——malformed fixture 漏了它就会测到另一条分支。
JSON_CT = {"content-type": "application/json"}

TRUNCATED_BODY = b'{"id": "chatcmpl-test", "choices": [{"index": 0, "mess'

VALID_OUTPUT = json.dumps(
    {
        "tables": [
            {
                "table_name": "orders",
                "topic": "orders",
                "description": "one row per order",
                "granularity": "one row per order",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _reset_shared_llm_client():
    llm_http.set_shared_llm_http_client(None)
    yield
    llm_http.set_shared_llm_http_client(None)


def _chat_completion(content: str, *, finish_reason: str = "stop") -> dict:
    """OpenAI 兼容的 chat.completion 响应体骨架。"""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "test/model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _expected_attempts() -> int:
    """SDK **传输层**重试次数 + 1。

    传输层重试用例一律用它而非硬编码，否则改 ``SDK_MAX_RETRIES`` 时会有多个用例
    同时失败、看不出哪条才是根事实（根事实由
    ``test_openai_client_max_retries_is_pinned_explicitly`` 单独把关）。
    **不要**拿它去推 Agent 层的 output/tool retries——两层独立。
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
        model = pa_llm.build_openrouter_chat_model("test/model")
    return model.client.max_retries + 1


def _run_against(response_factory) -> tuple[object, int]:
    """跑一次结构化输出调用，返回 (结果或异常, HTTP 请求次数)。

    ``response_factory`` 可返回 ``httpx.Response``，也可直接抛 httpx 传输层异常
    （用于模拟连接超时/中断）。
    """
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return response_factory()

    async def run() -> object:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm_http.set_shared_llm_http_client(client)
        try:
            agent = pa_llm.create_pa_agent(
                "cloud",
                cloud_model_id="test/model",
                instructions="classify",
                result_type=TableIntentBatch,
            )
            try:
                result = await agent.run(
                    PROMPT, model_settings=ModelSettings(timeout=10.0)
                )
                return result.output
            except Exception as e:  # noqa: BLE001 — 用例要断言异常本身
                return e
        finally:
            llm_http.set_shared_llm_http_client(None)
            await client.aclose()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
        outcome = asyncio.run(run())
    return outcome, calls["n"]


def test_empty_body_raises_bare_json_decode_error() -> None:
    """空 body（provider 连接被截断的典型形态）→ 裸 JSONDecodeError 逃逸。

    这正是 docs/evaluation.md 2026-07-25 行记录的 ``JSONDecodeError: Expecting value``。
    """
    outcome, calls = _run_against(
        lambda: httpx.Response(200, content=b"", headers=JSON_CT)
    )

    assert isinstance(outcome, json.JSONDecodeError)
    assert "Expecting value" in str(outcome)
    assert calls == 1, "畸形 200 响应不触发 SDK 重试"


def test_truncated_json_raises_bare_json_decode_error() -> None:
    outcome, calls = _run_against(
        lambda: httpx.Response(200, content=TRUNCATED_BODY, headers=JSON_CT)
    )

    assert isinstance(outcome, json.JSONDecodeError)
    assert "Unterminated string" in str(outcome)
    assert calls == 1


def test_json_decode_error_is_value_error_subclass() -> None:
    """决定它落进 ``pa_decision_step`` 的哪个 except 分支。

    ``JSONDecodeError`` 是 ``ValueError`` 子类 → 被
    ``except (ValueError, RuntimeError, TimeoutError)`` 捕获且 reason 非空；
    但 ``intent_analyzer`` 的 ``except Exception`` fail-open 会把它整个吞掉。
    """
    outcome, _ = _run_against(
        lambda: httpx.Response(200, content=b"", headers=JSON_CT)
    )

    assert isinstance(outcome, ValueError)
    assert str(outcome), "reason 必须非空"


def test_content_type_governs_which_failure_path() -> None:
    """把 mock 保真度陷阱本身钉住：同样的空 body，content-type 决定走哪条分支。

    缺 content-type 时 openai SDK 不调用 ``response.json()``，返回原始文本，
    最终由 pydantic-ai 包装成 ``UnexpectedModelBehavior``——**这不是真实上游行为**。
    本用例存在的意义是防止后来者再写出漏 content-type 的 fixture 并据此下结论。
    """
    with_ct, _ = _run_against(
        lambda: httpx.Response(200, content=b"", headers=JSON_CT)
    )
    without_ct, _ = _run_against(lambda: httpx.Response(200, content=b""))

    assert isinstance(with_ct, json.JSONDecodeError)
    assert isinstance(without_ct, UnexpectedModelBehavior)
    assert "expected JSON data" in str(without_ct)
    assert type(with_ct) is not type(without_ct)


def test_read_timeout_is_retried_by_sdk() -> None:
    """**这条是空串 ``llm_error: `` 的真实机制**（2026-07-28 round-2 评审补）。

    单次上游挂起 → ``ModelSettings(timeout=90s)`` 触发 → SDK 再重试 2 次 →
    最坏 3×90=270s，远超 ``_PA_TURN_TIMEOUT_S=150s`` 的外层 ``asyncio.timeout``
    → 抛出 ``str()`` 为空的裸 ``TimeoutError``。

    这条路径**不需要 5xx/429 参与**，也不需要多轮累加，因此比首版记录的
    「HTTP 5xx 重试」更贴近 eval 实际观测（日志中并无 5xx 记录）。
    注意异常类是 ``ModelAPIError`` 而非 ``ModelHTTPError``。
    """

    def raise_timeout():
        raise httpx.ReadTimeout("timed out")

    outcome, calls = _run_against(raise_timeout)

    assert isinstance(outcome, ModelAPIError)
    assert not isinstance(outcome, ModelHTTPError)
    assert calls == _expected_attempts()


def test_connect_error_is_retried_by_sdk() -> None:
    """连接中断同样被 SDK 重试，异常类同为 ``ModelAPIError``。"""

    def raise_connect():
        raise httpx.ConnectError("connection refused")

    outcome, calls = _run_against(raise_connect)

    assert isinstance(outcome, ModelAPIError)
    assert calls == _expected_attempts()


def test_http_5xx_is_retried_by_sdk() -> None:
    outcome, calls = _run_against(
        lambda: httpx.Response(500, json={"error": "upstream"}, headers=JSON_CT)
    )

    assert isinstance(outcome, ModelHTTPError)
    assert calls == _expected_attempts()


def test_http_429_is_retried_by_sdk() -> None:
    outcome, calls = _run_against(
        lambda: httpx.Response(429, json={"error": "rate limited"}, headers=JSON_CT)
    )

    assert isinstance(outcome, ModelHTTPError)
    assert calls == _expected_attempts()


def test_openrouter_error_envelope_in_200_body() -> None:
    """OpenRouter 对部分 provider 故障返回 HTTP 200 + error 信封。

    body 是合法 JSON 但不是 ChatCompletion，落到又一个异常类
    （``UnexpectedModelBehavior``，非 JSONDecodeError、非 ModelAPIError），
    且**不重试**。归类层必须覆盖这一形态。
    """
    outcome, calls = _run_against(
        lambda: httpx.Response(
            200,
            json={"error": {"code": 502, "message": "provider err"}},
            headers=JSON_CT,
        )
    )

    assert isinstance(outcome, UnexpectedModelBehavior)
    assert "validation error" in str(outcome)
    assert calls == 1


def test_openai_client_max_retries_is_pinned_explicitly() -> None:
    """``max_retries`` 必须是显式设定值，不吃 SDK 默认的 2。

    这是 PA 路径上唯一的 429/503 重试层（``llm.py`` 的 ``_post_with_retries``
    只覆盖非 PA 调用），同时又是把单轮拖过 ``_PA_TURN_TIMEOUT_S`` 的乘数。
    取值理由见 ``llm_pydantic_ai.SDK_MAX_RETRIES`` 的注释；本用例只防「回到默认值」。
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
        model = pa_llm.build_openrouter_chat_model("test/model")
        ollama = pa_llm.build_ollama_chat_model("test/model")

    assert model.client.max_retries == pa_llm.SDK_MAX_RETRIES
    assert ollama.client.max_retries == pa_llm.SDK_MAX_RETRIES, "本地路径须与云端对称"
    assert pa_llm.SDK_MAX_RETRIES != 2, "2 是 SDK 默认值，说明没真正显式设定"


def test_output_type_mismatch_is_retried_once() -> None:
    """真正的结构不匹配（``tables`` 类型错）会报错并重试一次，共 2 次请求。"""
    outcome, calls = _run_against(
        lambda: httpx.Response(
            200,
            json=_chat_completion(json.dumps({"tables": "not-a-list"})),
            headers=JSON_CT,
        )
    )

    assert isinstance(outcome, UnexpectedModelBehavior)
    assert "output retries" in str(outcome)
    # 2 = 首次 + Agent 层 output retry。**与 SDK 的 max_retries 无关**：那是传输层
    # 重试，这里的响应是 HTTP 200，SDK 不会重试。原先写作 `_expected_attempts() - 1`
    # 把两层耦合在了一起，SDK 侧一改就假失败。
    assert calls == 2


def test_finish_reason_error_is_rejected_before_safe_model_hook() -> None:
    """``finish_reason='error'`` 被 pydantic 的 ChatCompletion 校验先拦下。

    即 ``_SafeOpenAIChatModel._map_finish_reason`` 在真实路径上**不会被调用**
    （PR #30 的 override 是死代码）。若 SDK 未来放宽该字段校验，本用例的 spy
    断言会失败，提示 override 重新变成活代码、需复核。
    """
    seen: list[object] = []
    original = pa_llm._SafeOpenAIChatModel._map_finish_reason

    def spy(self, key):  # type: ignore[no-untyped-def]
        seen.append(key)
        return original(self, key)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pa_llm._SafeOpenAIChatModel, "_map_finish_reason", spy)
        outcome, _ = _run_against(
            lambda: httpx.Response(
                200,
                json=_chat_completion(VALID_OUTPUT, finish_reason="error"),
                headers=JSON_CT,
            )
        )

    assert isinstance(outcome, UnexpectedModelBehavior)
    assert "finish_reason" in str(outcome)
    assert seen == [], "当前 SDK 下 _map_finish_reason 不应被走到"


def test_valid_response_produces_output() -> None:
    """对照组：结构合法的响应正常产出，确保上面的失败断言不是构造错误所致。"""
    outcome, calls = _run_against(
        lambda: httpx.Response(
            200, json=_chat_completion(VALID_OUTPUT), headers=JSON_CT
        )
    )

    assert isinstance(outcome, TableIntentBatch)
    assert [t.table_name for t in outcome.tables] == ["orders"]
    assert calls == 1


def test_missing_optional_field_degrades_silently() -> None:
    """HTTP 200 + 合法 JSON + 缺少 ``tables`` 键 → 无异常、无重试、静默空结果。

    ``TableIntentBatch.tables`` 带 ``default_factory=list``，所以 ``{"not_tables": []}``
    能通过校验产出空批次（对比 ``test_output_type_mismatch_is_retried_once``：类型
    错才会报错重试）。这是 **schema 可选性设计**导致的降级，不是 pydantic-ai 行为，
    修 ``silent-output-shape-degradation`` 时应优先考虑把 ``tables`` 改为必填。
    """
    outcome, calls = _run_against(
        lambda: httpx.Response(
            200,
            json=_chat_completion(json.dumps({"not_tables": []})),
            headers=JSON_CT,
        )
    )

    assert isinstance(outcome, TableIntentBatch)
    assert outcome.tables == []
    assert calls == 1
