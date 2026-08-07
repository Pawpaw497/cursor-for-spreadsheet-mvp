"""GET /api/config contract and app lifespan startup warnings."""
from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import PRODUCT_DEFAULT_MODEL, settings as config_settings
from app.main import app, lifespan
from app.services import llm as llm_mod


@pytest.fixture(autouse=True)
def _reset_shared_llm_client() -> None:
    llm_mod.set_shared_llm_http_client(None)
    yield  # type: ignore[misc]
    llm_mod.set_shared_llm_http_client(None)


def test_config_exposes_single_product_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_settings, "OPENROUTER_MODEL", PRODUCT_DEFAULT_MODEL)
    monkeypatch.setattr(config_settings, "OPENROUTER_MODELS", PRODUCT_DEFAULT_MODEL)
    monkeypatch.setattr(config_settings, "OPENROUTER_LABELS", "DeepSeek: DeepSeek V4 Pro")

    data = TestClient(app).get("/api/config").json()

    assert data["openRouterModel"] == PRODUCT_DEFAULT_MODEL
    assert len(data["openRouterModels"]) == 1
    assert data["openRouterModels"][0]["id"] == PRODUCT_DEFAULT_MODEL
    assert data["openRouterModels"][0]["label"]


@pytest.mark.parametrize(
    ("model", "should_warn"),
    [
        (PRODUCT_DEFAULT_MODEL, False),
        ("openrouter/auto", True),
    ],
)
def test_lifespan_openrouter_model_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    model: str,
    should_warn: bool,
) -> None:
    monkeypatch.setattr(config_settings, "OPENROUTER_MODEL", model)
    monkeypatch.setattr("app.main._start_ollama", lambda: None)

    async def run() -> None:
        async with lifespan(app):
            pass

    with caplog.at_level(logging.WARNING, logger="app.main"):
        asyncio.run(run())

    warnings = [r for r in caplog.records if "running non-default model" in r.message]
    assert (len(warnings) > 0) == should_warn
    if should_warn:
        assert PRODUCT_DEFAULT_MODEL in warnings[0].message
        assert model in warnings[0].message
