"""LLM 调用：Ollama / OpenRouter。"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.services.prompts import Message, extract_json

# 带 tools 时返回：(content 或 None, tool_calls 或 None)
# tool_calls: list[dict] 每项 {"id": str, "name": str, "arguments": str}
LLMWithToolsResult = tuple[str | None, list[dict] | None]


def _messages_to_payload(messages: list[Message]) -> list[dict]:
    return [m.to_dict() for m in messages]


def _messages_with_tools_to_payload(messages: list[dict]) -> list[dict]:
    """将支持 tool_calls / tool 的 message 列表转为 API 所需格式。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "user")
        msg: dict[str, Any] = {"role": role}
        if role == "tool":
            msg["content"] = m.get("content", "")
            if m.get("tool_call_id"):
                msg["tool_call_id"] = m["tool_call_id"]
        else:
            if m.get("content") is not None:
                msg["content"] = m["content"]
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
        out.append(msg)
    return out


def _parse_tool_calls_from_response(raw: list[dict] | None) -> list[dict] | None:
    """从 API 返回的 tool_calls 转为 [{"id", "name", "arguments"}]。"""
    if not raw:
        return None
    result = []
    for tc in raw:
        fn = tc.get("function") or {}
        result.append({
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "{}"),
        })
    return result if result else None


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


async def call_openrouter_with_tools(
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
) -> LLMWithToolsResult:
    """OpenRouter 带 tools 的调用；返回 (content, tool_calls)。"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.1,
        "messages": _messages_with_tools_to_payload(messages),
        "tools": tools,
        "tool_choice": "auto",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter error: {r.text}")
        data = r.json()
    msg = data.get("choices", [{}])[0].get("message", {})
    content = msg.get("content") or None
    if isinstance(content, str) and not content.strip():
        content = None
    raw_tc = msg.get("tool_calls")
    tool_calls = _parse_tool_calls_from_response(raw_tc)
    return (content, tool_calls)


async def call_ollama_with_tools(
    model: str,
    messages: list[dict],
    tools: list[dict],
) -> LLMWithToolsResult:
    """Ollama 带 tools 的调用。部分模型支持；不支持时退化为无 tool_calls。"""
    # Ollama 部分版本 / 模型支持 tools，格式与 OpenAI 类似
    payload: dict[str, Any] = {
        "model": model,
        "messages": _messages_with_tools_to_payload(messages),
        "stream": False,
        "tools": tools,
    }
    url = f"{settings.OLLAMA_BASE}/api/chat"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, json=payload, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text}")
        data = r.json()
    msg = data.get("message", {})
    content = (msg.get("content") or "").strip() or None
    raw_tc = msg.get("tool_calls")
    tool_calls = _parse_tool_calls_from_response(raw_tc)
    return (content, tool_calls)


async def call_llm_with_tools(
    model_source: str,
    messages: list[dict],
    tools: list[dict],
    *,
    cloud_model_id: str | None = None,
    local_model_id: str | None = None,
) -> LLMWithToolsResult:
    """带 tools 的 LLM 调用；返回 (content 或 None, tool_calls 或 None)。"""
    src = (model_source or "cloud").lower()
    if src == "local":
        model = local_model_id or settings.OLLAMA_MODEL
        return await call_ollama_with_tools(model=model, messages=messages, tools=tools)
    if src == "cloud":
        api_key = settings.OPENROUTER_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY missing")
        model = cloud_model_id or settings.OPENROUTER_MODEL
        return await call_openrouter_with_tools(
            api_key=api_key, model=model, messages=messages, tools=tools
        )
    raise ValueError(f"Unknown modelSource: {model_source}")


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
