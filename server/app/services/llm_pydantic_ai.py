"""Pydantic AI model/agent factory for OpenRouter (cloud) and Ollama (local).

Phase 1 wrapper: mirrors ``llm.call_llm`` model resolution and reuses the shared
``httpx.AsyncClient`` from ``llm`` when registered. Graph nodes and tools wire in later phases.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.config import settings
from app.services import llm as llm_http

ModelSource = Literal["cloud", "local"]

ResultT = TypeVar("ResultT")

# openai SDK 的传输层重试次数。**显式设定，不吃默认值 2**。
#
# 保留 1 次而非 0：PA 路径上这是**唯一**的 429/503 重试层（`llm.py` 的
# `_post_with_retries` 只覆盖非 PA 调用），置 0 等于一次瞬时限流就 FinishAction 收场。
# 取 1 而非 2：2 次重试配 90s per-attempt 会把单轮拖过 `_PA_TURN_TIMEOUT_S`，
# 抛出 `str()` 为空的裸 TimeoutError——即用户看到的空串 `llm_error: `。
# 该取值与 `_PA_PER_ATTEMPT_TIMEOUT_S` 联动，改动前先跑
# `tests/test_pa_decision_timeout_budget.py`（实测标定，不是算术证明）。
SDK_MAX_RETRIES = 1

# Ollama provider 在没有 API key 时要求一个非空占位值（与其内部默认一致）。
_OLLAMA_PLACEHOLDER_API_KEY = "api-key-not-set"

# pydantic-ai ``Agent`` 层的重试预算，与上面的 SDK 传输层重试**互相独立**。
#
# 适用范围有限：只有用 ``agent.run()`` 的子 agent（intent_analyzer / pre_plan /
# clarify_scan / preview_summary）会走到它。``pa_decision`` 用 ``agent.iter()`` 并在
# 第一个 CallToolsNode 处 break（Approach A），输出校验与工具执行都在该节点之后，
# 因此这两个预算在主决策路径上**不可达**（见
# ``test_llm_pydantic_ai.py::test_agent_layer_retries_unreachable_on_iter_break_path``）。
#
# tools=0：本项目从不在 pydantic-ai 的 run 内执行工具，该预算无处生效；显式写 0
#   把「不可达」变成明示，而不是留一个随时可能被唤醒的默认值。
# output=1：子 agent 的结构化输出偶发 schema 不符时，同一轮内重试一次是最便宜的修复。
#   注意 ``pa_decision`` 另有 ``MAX_PLAN_VALIDATION_RETRIES`` 的外层递归，两者输入不同
#   （内层同一次 run 内重发，外层带 feedback 重构一轮），不是同一件事。
#
# 写法用 ``retries={...}``：``Agent(output_retries=…)`` / ``Agent(tool_retries=…)``
# 在 pydantic-ai 1.x 已废弃。
AGENT_RETRIES: dict[str, int] = {"tools": 0, "output": 1}


def openrouter_base_url() -> str:
    """``OpenRouterProvider.base_url`` 的值（它是实例 property，此处按类读取）。

    自建 ``AsyncOpenAI`` 时 provider 不再负责设 base_url，必须由我们传。
    单测锁住本函数与 provider 的一致性，防止上游改地址后静默漂移。
    """
    return str(OpenRouterProvider.base_url.fget(OpenRouterProvider))  # type: ignore[attr-defined]


def _openrouter_attribution_headers(app_title: str | None) -> dict[str, str]:
    """复刻 ``OpenRouterProvider`` 的归因 header 装配。

    provider 只在 ``openai_client is None`` 的分支里装配 ``HTTP-Referer`` / ``X-Title``；
    我们为了注入 ``max_retries`` 必须自建 client，于是整段被跳过——不手动补齐会静默
    丢掉 ``settings.APP_TITLE`` 归因。
    """
    headers: dict[str, str] = {}
    if http_referer := os.getenv("OPENROUTER_APP_URL"):
        headers["HTTP-Referer"] = http_referer
    if x_title := (app_title or settings.APP_TITLE or os.getenv("OPENROUTER_APP_TITLE")):
        headers["X-Title"] = x_title
    return headers


@dataclass(frozen=True, slots=True)
class ResolvedPaModel:
    """Resolved LLM backend and model id (aligned with ``call_llm``)."""

    source: ModelSource
    model: str


def resolve_pa_model(
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> ResolvedPaModel:
    """Resolve ``model_source`` + optional overrides to cloud/local model id."""
    src = (model_source or "cloud").lower()
    if src == "local":
        return ResolvedPaModel(source="local", model=local_model_id or settings.OLLAMA_MODEL)
    if src == "cloud":
        if not settings.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY missing")
        return ResolvedPaModel(
            source="cloud",
            model=cloud_model_id or settings.OPENROUTER_MODEL,
        )
    raise ValueError(f"Unknown modelSource: {model_source}")


def ollama_openai_base_url(base: str | None = None) -> str:
    """Map app ``OLLAMA_BASE`` (native API root) to OpenAI-compatible ``/v1`` root."""
    root = (base or settings.OLLAMA_BASE).rstrip("/")
    if root.endswith("/v1"):
        return root
    return f"{root}/v1"


def _shared_http_client() -> httpx.AsyncClient | None:
    return llm_http.get_shared_llm_http_client()


def build_openrouter_chat_model(
    model: str,
    *,
    api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    app_title: str | None = None,
) -> OpenAIChatModel:
    """OpenRouter-backed ``OpenAIChatModel`` (OpenAI-compatible chat completions)."""
    key = api_key if api_key is not None else settings.OPENROUTER_API_KEY
    if not key:
        raise ValueError("OPENROUTER_API_KEY missing")
    client = http_client if http_client is not None else _shared_http_client()
    # `max_retries` 只能在 AsyncOpenAI 上设置——OpenAIChatModel / provider 都不接受它。
    # 传 `openai_client=` 会绕过 provider 的 base_url + 归因 header 装配，故手动补齐。
    openai_client = AsyncOpenAI(
        base_url=openrouter_base_url(),
        api_key=key,
        http_client=client,
        default_headers=_openrouter_attribution_headers(app_title),
        max_retries=SDK_MAX_RETRIES,
    )
    return OpenAIChatModel(model, provider=OpenRouterProvider(openai_client=openai_client))


def build_ollama_chat_model(
    model: str,
    *,
    base_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> OpenAIChatModel:
    """Ollama-backed ``OpenAIChatModel`` via OpenAI-compatible ``/v1`` API."""
    client = http_client if http_client is not None else _shared_http_client()
    # 与 OpenRouter 对称地钉住 `max_retries`（OllamaProvider 内部同样吃默认值 2）。
    # 注意失败模式不同：OllamaProvider 在 `openai_client is not None` 分支里断言
    # base_url / http_client / api_key **全为 None**，传了会当场 AssertionError，
    # 因此三者都必须搬进自建的 AsyncOpenAI，provider 只收 openai_client。
    openai_client = AsyncOpenAI(
        base_url=ollama_openai_base_url(base_url),
        api_key=_OLLAMA_PLACEHOLDER_API_KEY,
        http_client=client,
        max_retries=SDK_MAX_RETRIES,
    )
    return OpenAIChatModel(model, provider=OllamaProvider(openai_client=openai_client))


def build_chat_model(
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> OpenAIChatModel:
    """Build a Pydantic AI chat model for cloud (OpenRouter) or local (Ollama)."""
    resolved = resolve_pa_model(
        model_source,
        cloud_model_id=cloud_model_id,
        local_model_id=local_model_id,
    )
    if resolved.source == "local":
        return build_ollama_chat_model(
            resolved.model,
            http_client=http_client,
        )
    return build_openrouter_chat_model(
        resolved.model,
        http_client=http_client,
    )


def create_pa_agent(
    model_source: str,
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    instructions: str | None = None,
    result_type: type[ResultT] | None = None,
    **agent_kwargs: Any,
) -> Agent[None, ResultT] | Agent[None, str]:
    """Create a Pydantic AI ``Agent`` wired to OpenRouter or Ollama."""
    model = build_chat_model(
        model_source,
        cloud_model_id=cloud_model_id,
        local_model_id=local_model_id,
        http_client=http_client,
    )
    agent_kwargs.setdefault("retries", dict(AGENT_RETRIES))
    if result_type is not None:
        return Agent(model, instructions=instructions, output_type=result_type, **agent_kwargs)
    return Agent(model, instructions=instructions, **agent_kwargs)
