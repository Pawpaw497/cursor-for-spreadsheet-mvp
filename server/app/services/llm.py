"""LLM 调用：Ollama / OpenRouter。"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger
from app.services.prompts import Message

log = get_logger("services.llm")

# 带 tools 时返回：(content 或 None, tool_calls 或 None)
# tool_calls: list[dict] 每项 {"id": str, "name": str, "arguments": str}
LLMWithToolsResult = tuple[str | None, list[dict] | None]


def _message_stats(messages: list[Message]) -> tuple[int, int]:
    """返回消息条数与内容字符总数（用于日志，不落库全文）。"""
    n = len(messages)
    total = sum(len(m.content or "") for m in messages)
    return n, total


def _dict_message_stats(messages: list[dict]) -> tuple[int, int]:
    """dict 形态 messages 的条数与内容字符估计。"""
    n = len(messages)
    total = sum(len(str(m.get("content") or "")) for m in messages)
    return n, total


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


def _raise_openrouter_error(resp: httpx.Response) -> None:
    """解析 OpenRouter 错误响应并抛出结构化 RuntimeError。

    对 401/403 等鉴权错误增加 AUTH_ERROR 前缀，便于上层路由区分并返回更友好的提示。

    Args:
        resp: OpenRouter HTTP 响应对象。

    Raises:
        RuntimeError: 总是抛出，消息中包含精简的人类可读文案和原始响应片段。
    """
    status = resp.status_code
    body_text = resp.text or "<empty body>"

    error_code: Any | None = None
    error_message: str | None = None

    try:
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            err = data["error"]
            error_code = err.get("code")
            if isinstance(error_code, dict):
                # 极端情况下 code 也是嵌套结构，这里做一次保护性展开。
                error_code = err.get("code", {}).get("code")
            if isinstance(err.get("message"), str):
                error_message = err["message"]
    except Exception:
        data = None  # noqa: F841  # 仅用于调试时临时打印，不在这里使用

    human_detail = error_message or body_text
    base_msg = f"OpenRouter HTTP {status}: {human_detail}"

    # 针对典型鉴权错误增加前缀，后续路由可据此返回更友好的中文提示。
    is_auth_error = status in (401, 403)
    if isinstance(error_code, (int, str)) and str(error_code) in {"401", "403"}:
        is_auth_error = True

    if is_auth_error:
        raise RuntimeError(f"AUTH_ERROR: {base_msg}. Raw: {body_text}")

    raise RuntimeError(f"{base_msg}. Raw: {body_text}")


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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            _raise_openrouter_error(r)
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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            _raise_openrouter_error(r)
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
    elif src == "cloud":
        if not settings.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY missing")
        model = cloud_model_id or settings.OPENROUTER_MODEL
    else:
        raise ValueError(f"Unknown modelSource: {model_source}")

    n_msg, n_chars = _dict_message_stats(messages)
    log.info(
        "llm_with_tools start source=%s model=%s messages=%d content_chars=%d tools=%d",
        src,
        model,
        n_msg,
        n_chars,
        len(tools),
    )
    t0 = time.perf_counter()
    try:
        if src == "local":
            content, tool_calls = await call_ollama_with_tools(
                model=model, messages=messages, tools=tools
            )
        else:
            content, tool_calls = await call_openrouter_with_tools(
                api_key=settings.OPENROUTER_API_KEY,
                model=model,
                messages=messages,
                tools=tools,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        has_tools = bool(tool_calls)
        content_len = len(content) if content else 0
        log.info(
            "llm_with_tools done source=%s model=%s elapsed_ms=%.2f has_tool_calls=%s content_chars=%d",
            src,
            model,
            elapsed_ms,
            has_tools,
            content_len,
        )
        return (content, tool_calls)
    except Exception:
        log.exception(
            "llm_with_tools failed source=%s model=%s elapsed_ms=%.2f",
            src,
            model,
            (time.perf_counter() - t0) * 1000,
        )
        raise


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
    elif src == "cloud":
        if not settings.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY missing")
        model = cloud_model_id or settings.OPENROUTER_MODEL
    else:
        raise ValueError(f"Unknown modelSource: {model_source}")

    n_msg, n_chars = _message_stats(messages)
    log.info(
        "llm call start source=%s model=%s messages=%d content_chars=%d",
        src,
        model,
        n_msg,
        n_chars,
    )
    t0 = time.perf_counter()
    try:
        if src == "local":
            out = await call_ollama(model=model, messages=messages)
        else:
            out = await call_openrouter(
                api_key=settings.OPENROUTER_API_KEY,
                model=model,
                messages=messages,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(
            "llm call done source=%s model=%s elapsed_ms=%.2f response_chars=%d",
            src,
            model,
            elapsed_ms,
            len(out or ""),
        )
        return out
    except Exception:
        log.exception(
            "llm call failed source=%s model=%s elapsed_ms=%.2f",
            src,
            model,
            (time.perf_counter() - t0) * 1000,
        )
        raise
