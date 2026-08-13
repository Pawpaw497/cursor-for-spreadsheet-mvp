"""自建 ``AsyncOpenAI`` 注入 provider 的回归防线。

为了显式设定 ``max_retries``，两个 provider 都改成收 ``openai_client=``。这条路径
上有两个**方向相反**的坑，各自需要一条断言：

* **OpenRouter 静默降级** —— ``OpenRouterProvider.__init__`` 只在
  ``openai_client is None`` 的分支里装配 ``base_url`` 与归因 header
  （``X-Title`` / ``HTTP-Referer``）。传 client 即整段跳过，``APP_TITLE`` 无声消失，
  没有任何报错。
* **Ollama 硬失败** —— ``OllamaProvider.__init__`` 在同一分支里断言
  ``base_url`` / ``http_client`` / ``api_key`` **全为 None**，传了当场 AssertionError。

另有一条与本文件同源的性质：Agent 层的重试预算在 ``pa_decision`` 的
``iter`` + break 路径上不可达——它决定了 ``AGENT_RETRIES`` 的适用范围，也决定了
「重试乘数」的算法边界，故一并锁在这里。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

from app.models.plan import Plan
from app.services import llm as llm_http
from app.services import llm_pydantic_ai as pa_llm


@pytest.fixture(autouse=True)
def _reset_shared_llm_client():
    llm_http.set_shared_llm_http_client(None)
    yield
    llm_http.set_shared_llm_http_client(None)


def test_openrouter_attribution_headers_survive_client_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自建 client 后仍须带上 ``X-Title``——provider 已不再负责装配它。"""
    monkeypatch.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(pa_llm.settings, "APP_TITLE", "Cursor for Spreadsheet")

    model = pa_llm.build_openrouter_chat_model("test/model")

    assert model.client.default_headers.get("X-Title") == "Cursor for Spreadsheet"


def test_openrouter_base_url_matches_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url 现在由我们传，须与 provider 自己的值一致（防上游改地址后漂移）。"""
    monkeypatch.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")

    model = pa_llm.build_openrouter_chat_model("test/model")

    expected = OpenRouterProvider(openai_client=model.client).base_url
    assert pa_llm.openrouter_base_url() == expected
    assert str(model.client.base_url).rstrip("/") == expected.rstrip("/")


def test_ollama_provider_rejects_client_plus_base_url() -> None:
    """钉住 Ollama 的硬失败语义：``openai_client`` 与其余三者互斥。

    这条不是测我们的代码，而是测「为什么 Ollama 不能照抄 OpenRouter 的写法」。
    上游哪天放宽了这个断言，本用例会红，提示可以简化 ``build_ollama_chat_model``。
    """
    client = pa_llm.AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="x")
    with pytest.raises(AssertionError):
        OllamaProvider(openai_client=client, base_url="http://localhost:11434/v1")


def test_ollama_model_keeps_base_url_and_shared_client() -> None:
    """搬进自建 client 之后，base_url 与共享连接池都不能丢。"""
    shared = httpx.AsyncClient()
    llm_http.set_shared_llm_http_client(shared)
    try:
        model = pa_llm.build_ollama_chat_model("qwen3", base_url="http://host:11434")
        assert str(model.client.base_url).rstrip("/").endswith("/v1")
        assert model.client._client is shared
    finally:
        llm_http.set_shared_llm_http_client(None)
        asyncio.run(shared.aclose())


def _bad_final_result() -> httpx.Response:
    """模型调用 ``final_result`` 但参数不符 ``Plan`` schema。"""
    body = {
        "id": "x",
        "object": "chat.completion",
        "created": 1,
        "model": "test/model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "final_result",
                                "arguments": json.dumps({"nonsense": 1}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return httpx.Response(200, json=body, headers={"content-type": "application/json"})


def _count_requests(mode: str, monkeypatch: pytest.MonkeyPatch) -> int:
    calls = {"n": 0}

    async def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _bad_final_result()

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm_http.set_shared_llm_http_client(client)
        try:
            agent = pa_llm.create_pa_agent(
                "cloud", cloud_model_id="test/model", instructions="plan", result_type=Plan
            )
            settings = ModelSettings(timeout=10.0)
            try:
                if mode == "run":
                    await agent.run("hi", model_settings=settings)
                else:
                    async with agent.iter("hi", model_settings=settings) as it:
                        async for node in it:
                            if Agent.is_call_tools_node(node):
                                break
            except Exception:  # noqa: BLE001 — 本用例只关心请求次数
                pass
        finally:
            llm_http.set_shared_llm_http_client(None)
            await client.aclose()

    monkeypatch.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")
    asyncio.run(run())
    return calls["n"]


def test_agent_layer_retries_unreachable_on_iter_break_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pa_decision`` 的 iter + break 使 Agent 层 output retry 不可达。

    这个性质很隐蔽也很脆：把 ``_run_pa_single_turn`` 改成 ``agent.run()``、或把 break
    点挪到 CallToolsNode 之后，重试乘数就会**静默恢复**，单轮最坏耗时随之翻倍，而
    没有任何一条现有用例会红。历次计划反复在「重试到底乘几层」上判断失误，根源就是
    这里只有读代码、没有可执行的断言。
    """
    assert _count_requests("run", monkeypatch) == 2, "agent.run() 应发生一次 output retry"
    assert _count_requests("iter-break", monkeypatch) == 1, (
        "iter + 在 CallToolsNode 处 break 不应触发 Agent 层重试"
    )


def test_agent_retries_are_pinned_not_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent 层预算须显式设定，且用未废弃的 ``retries=`` 写法。"""
    monkeypatch.setattr(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test")

    agent = pa_llm.create_pa_agent("cloud", cloud_model_id="test/model")

    assert agent._max_tool_retries == pa_llm.AGENT_RETRIES["tools"]
    assert agent._max_output_retries == pa_llm.AGENT_RETRIES["output"]
