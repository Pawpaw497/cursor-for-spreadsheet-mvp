"""LLM 调用：Ollama / OpenRouter。"""
from __future__ import annotations

import httpx

from app.config import settings
from app.services.prompts import Message, extract_json


def _messages_to_payload(messages: list[Message]) -> list[dict]:
    return [m.to_dict() for m in messages]


async def call_ollama(model: str, messages: list[Message]) -> str:
    base = settings.OLLAMA_BASE
    url = f"{base}/api/chat"
    payload = {
        "model": model,
        "messages": _messages_to_payload(messages),
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, json=payload, timeout=120)
        if r.status_code >= 400:
            detail = r.text or "<empty body>"
            raise RuntimeError(f"Ollama error {r.status_code}: {detail}")
        data = r.json()
        return data.get("message", {}).get("content", "")


async def call_openrouter(api_key: str, model: str, messages: list[Message]) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": _messages_to_payload(messages),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter error: {r.text}")
        data = r.json()
        return data["choices"][0]["message"]["content"]


async def call_llm(
    model_source: str,
    messages: list[Message],
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> str:
    """根据 model_source 调用本地或云端 LLM；messages 支持多轮对话。"""
    src = (model_source or "cloud").lower()
    if src == "local":
        model = local_model_id or settings.OLLAMA_MODEL
        return await call_ollama(model=model, messages=messages)
    if src == "cloud":
        api_key = settings.OPENROUTER_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY missing")
        model = cloud_model_id or settings.OPENROUTER_MODEL
        return await call_openrouter(
            api_key=api_key, model=model, messages=messages
        )
    raise ValueError(f"Unknown modelSource: {model_source}")
