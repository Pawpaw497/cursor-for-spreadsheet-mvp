"""Pydantic AI dependency smoke and OpenRouter/Ollama factory wiring."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.services import llm as llm_http
from app.services import llm_pydantic_ai as pa_llm


@pytest.fixture(autouse=True)
def _reset_shared_llm_client() -> None:
    llm_http.set_shared_llm_http_client(None)
    yield  # type: ignore[misc]
    llm_http.set_shared_llm_http_client(None)


def test_pydantic_ai_import_smoke() -> None:
    import pydantic_ai  # noqa: F401

    from pydantic_ai.providers.ollama import OllamaProvider
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    assert pydantic_ai.__version__
    assert OpenRouterProvider is not None
    assert OllamaProvider is not None


def test_ollama_openai_base_url_appends_v1() -> None:
    assert pa_llm.ollama_openai_base_url("http://localhost:11434") == (
        "http://localhost:11434/v1"
    )
    assert pa_llm.ollama_openai_base_url("http://localhost:11434/v1") == (
        "http://localhost:11434/v1"
    )


def test_resolve_pa_model_cloud_and_local() -> None:
    with patch.object(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test"):
        cloud = pa_llm.resolve_pa_model("cloud", cloud_model_id="openai/gpt-4o-mini")
        assert cloud.source == "cloud"
        assert cloud.model == "openai/gpt-4o-mini"

    local = pa_llm.resolve_pa_model("local", local_model_id="qwen2.5:7b")
    assert local.source == "local"
    assert local.model == "qwen2.5:7b"


def test_resolve_pa_model_cloud_requires_api_key() -> None:
    with patch.object(pa_llm.settings, "OPENROUTER_API_KEY", ""):
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            pa_llm.resolve_pa_model("cloud")


def test_build_openrouter_chat_model_uses_openrouter_provider() -> None:
    with patch.object(pa_llm.settings, "OPENROUTER_API_KEY", "sk-or-test"):
        model = pa_llm.build_openrouter_chat_model("openai/gpt-4o-mini")
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "openai/gpt-4o-mini"
    assert model._provider.name == "openrouter"  # type: ignore[attr-defined]
    assert model._provider.base_url == "https://openrouter.ai/api/v1"  # type: ignore[attr-defined]


def test_build_ollama_chat_model_uses_v1_base() -> None:
    with patch.object(pa_llm.settings, "OLLAMA_BASE", "http://127.0.0.1:11434"):
        model = pa_llm.build_ollama_chat_model("qwen2.5:7b")
    assert model.model_name == "qwen2.5:7b"
    assert model._provider.name == "ollama"  # type: ignore[attr-defined]
    assert str(model._provider.base_url).rstrip("/") == "http://127.0.0.1:11434/v1"  # type: ignore[attr-defined]


def test_build_chat_model_passes_shared_http_client() -> None:
    import asyncio

    from pydantic_ai.providers.openrouter import OpenRouterProvider as RealOpenRouterProvider

    client = httpx.AsyncClient()
    llm_http.set_shared_llm_http_client(client)
    try:
        with (
            patch.object(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test"),
            patch.object(
                pa_llm,
                "OpenRouterProvider",
                wraps=RealOpenRouterProvider,
            ) as provider_cls,
        ):
            model = pa_llm.build_chat_model("cloud", cloud_model_id="openai/gpt-4o-mini")
        provider_cls.assert_called_once()
        # 为了注入 max_retries，共享 client 现在传给自建的 AsyncOpenAI，
        # provider 只收 openai_client=（它对 openai_client 与 http_client 二选一）。
        assert "http_client" not in provider_cls.call_args.kwargs
        assert provider_cls.call_args.kwargs["openai_client"]._client is client
        assert model.client._client is client
    finally:
        llm_http.set_shared_llm_http_client(None)
        asyncio.run(client.aclose())


def test_create_pa_agent_returns_agent() -> None:
    with patch.object(pa_llm.settings, "OPENROUTER_API_KEY", "sk-test"):
        agent = pa_llm.create_pa_agent(
            "cloud",
            cloud_model_id="openai/gpt-4o-mini",
            instructions="test",
        )
    assert isinstance(agent, Agent)
