"""Best-effort SQLite audit logging for HTTP requests and LLM calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.logging_config import get_logger, get_trace_id
from app.services import audit_db
from app.services.llm_debug_log import (
    build_error_payload,
    build_result_payload,
    prepare_messages_for_log,
    tool_names_from_spec,
)

log = get_logger("services.audit_log")


def is_audit_enabled() -> bool:
    return bool(settings.AUDIT_DB_ENABLED)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _max_body_chars() -> int:
    return max(1, int(settings.AUDIT_MAX_BODY_CHARS))


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _serialize_json(value: Any, *, max_chars: int | None = None) -> str | None:
    if value is None:
        return None
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(value)
    limit = max_chars if max_chars is not None else _max_body_chars()
    return _truncate_text(raw, limit)


def workspace_key_hash(raw_key: str | None) -> str | None:
    """Hash workspace key for audit storage; never store plaintext unless explicitly enabled."""
    key = (raw_key or "").strip()
    if not key:
        return None
    if settings.AUDIT_STORE_WORKSPACE_KEY:
        return _truncate_text(key, 128)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def infer_request_kind(path: str) -> str | None:
    p = path.rstrip("/") or "/"
    if p == "/health":
        return "health"
    if p == "/api/load-sample":
        return "load_sample"
    if p == "/api/import-file":
        return "import"
    if p.endswith("/export-excel") or p == "/api/export-excel":
        return "export"
    if "/execute-plan" in p or p.endswith("/execute-plan"):
        return "execute"
    if p.endswith("/agent-stream") or p.endswith("/agent"):
        return "agent"
    if p.startswith("/api/sessions"):
        return "session_sync"
    if p == "/api/data/upload":
        return "data_upload"
    if "/plan" in p:
        return "plan"
    return None


def infer_workspace_kind(path: str, request_kind: str | None) -> str | None:
    if request_kind == "load_sample":
        return "builtin_sample"
    if request_kind == "import":
        return "uploaded_file"
    return None


def _extract_from_mapping(data: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {
        "project_id": None,
        "session_id": None,
        "model_tag": None,
    }
    for key in ("projectId", "project_id"):
        if data.get(key):
            out["project_id"] = str(data[key])
            break
    for key in ("sessionId", "session_id"):
        if data.get(key):
            out["session_id"] = str(data[key])
            break
    for key in ("modelTag", "model_tag"):
        if data.get(key):
            out["model_tag"] = str(data[key])
            break
    return out


def extract_audit_context(
    request: Request | None = None,
    *,
    body: Any = None,
    path: str | None = None,
) -> dict[str, str | None]:
    """Collect trace/session/project/model_tag/workspace fields from headers and JSON body."""
    ctx: dict[str, str | None] = {
        "trace_id": get_trace_id(),
        "project_id": None,
        "session_id": None,
        "model_tag": None,
        "workspace_key_hash": None,
        "workspace_kind": None,
        "request_kind": None,
    }
    req_path = path or (request.url.path if request else "")
    ctx["request_kind"] = infer_request_kind(req_path)
    ctx["workspace_kind"] = infer_workspace_kind(req_path, ctx["request_kind"])

    if request is not None:
        session_hdr = (request.headers.get("X-Session-ID") or "").strip()
        if session_hdr:
            ctx["session_id"] = session_hdr
        tag_hdr = (request.headers.get("X-Model-Tag") or "").strip()
        if tag_hdr:
            ctx["model_tag"] = tag_hdr
        ws_hdr = (request.headers.get("X-Workspace-Key") or "").strip()
        if ws_hdr:
            ctx["workspace_key_hash"] = workspace_key_hash(ws_hdr)

    if isinstance(body, dict):
        mapped = _extract_from_mapping(body)
        for k, v in mapped.items():
            if v and not ctx.get(k):
                ctx[k] = v

    return ctx


# Strong references to in-flight audit tasks. ``loop.create_task`` only keeps a weak
# reference, so a caller that drops the returned task lets the GC collect it mid-flight
# -- an audit row would vanish with no log line at all. Entries remove themselves on
# completion, so the set stays bounded by the number of concurrent audit writes.
_background_tasks: set[asyncio.Task[Any]] = set()


def _schedule(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        import threading

        def _run() -> None:
            try:
                asyncio.run(coro)
            except Exception as e:
                log.warning("audit_log background write failed: %s", e)

        threading.Thread(target=_run, daemon=True).start()
        return

    task = loop.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def drain_background_tasks(timeout: float = 30.0) -> None:
    """Await audit writes scheduled on the running loop; never raises.

    Exists so callers (tests, shutdown paths) can wait deterministically instead of
    guessing a sleep duration -- ``_insert_with_retry`` backs off up to 1s per attempt.
    Task failures are already downgraded to WARNING inside ``record_*``; anything left
    is swallowed here so draining cannot break the fire-and-forget contract.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        pending = [
            t
            for t in _background_tasks
            if not t.done() and t.get_loop() is loop
        ]
        if not pending:
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            log.warning(
                "drain_background_tasks timed out with %d audit write(s) pending",
                len(pending),
            )
            return
        await asyncio.wait(pending, timeout=remaining)


# Bounded retry for lock contention the engine's busy_timeout could not absorb (e.g.
# SQLITE_BUSY_SNAPSHOT in WAL, which the busy handler does not retry).
_MAX_INSERT_ATTEMPTS = 6
_RETRY_BASE_DELAY_S = 0.05
_RETRY_MAX_DELAY_S = 1.0


def _is_sqlite_locked(exc: BaseException) -> bool:
    """True for SQLite lock contention only; other failures must not be retried."""
    if not isinstance(exc, (OperationalError, sqlite3.OperationalError)):
        return False
    return "locked" in str(exc).lower()


async def _insert_with_retry(model: type[Any], /, **fields: Any) -> None:
    """Insert one audit row, retrying only on SQLite lock contention.

    Each attempt rebuilds both the session and the ORM instance: after a failed commit
    the session is rolled back and the instance detached, so retrying ``commit()`` on
    the originals would be a no-op. Non-lock errors and a final exhausted attempt are
    re-raised for the caller's ``except`` to downgrade to a WARNING -- audit failures
    must never surface to the business request.
    """
    factory = audit_db.get_session_factory()
    if factory is None:
        return
    for attempt in range(_MAX_INSERT_ATTEMPTS):
        try:
            async with factory() as session:
                session.add(model(**fields))
                await session.commit()
            return
        except Exception as e:
            if _is_sqlite_locked(e) and attempt < _MAX_INSERT_ATTEMPTS - 1:
                delay = min(_RETRY_BASE_DELAY_S * (2**attempt), _RETRY_MAX_DELAY_S)
                log.debug(
                    "audit insert locked, retrying in %.2fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    _MAX_INSERT_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue
            raise


async def _insert_http_row(**fields: Any) -> None:
    await _insert_with_retry(audit_db.HttpRequestLog, **fields)


async def _insert_llm_row(**fields: Any) -> None:
    await _insert_with_retry(audit_db.LlmCallLog, **fields)


async def record_http_request(
    *,
    trace_id: str,
    method: str,
    path: str,
    query_params: dict[str, Any] | None = None,
    request_body: Any = None,
    response_status: int | None = None,
    response_body: Any = None,
    error_detail: str | None = None,
    duration_ms: float | None = None,
    client_host: str | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
    workspace_key_hash: str | None = None,
    workspace_kind: str | None = None,
    model_tag: str | None = None,
    request_kind: str | None = None,
) -> None:
    """Persist one HTTP audit row; callers should schedule via ``schedule_record_http_request``."""
    if not is_audit_enabled():
        return
    try:
        await _insert_http_row(
            trace_id=trace_id or "-",
            project_id=project_id,
            session_id=session_id,
            workspace_key_hash=workspace_key_hash,
            workspace_kind=workspace_kind,
            model_tag=model_tag,
            method=method,
            path=path,
            query_params=_serialize_json(dict(query_params) if query_params else None),
            request_body=_serialize_json(request_body),
            response_status=response_status,
            response_body=_serialize_json(response_body),
            error_detail=_truncate_text(error_detail, _max_body_chars())
            if error_detail
            else None,
            duration_ms=duration_ms,
            client_host=client_host,
            request_kind=request_kind,
            created_at=_utc_now_iso(),
        )
    except Exception as e:
        log.warning("record_http_request failed: %s", e)


def schedule_record_http_request(**kwargs: Any) -> None:
    """Fire-and-forget HTTP audit write; never raises."""
    if not is_audit_enabled():
        return
    try:
        _schedule(record_http_request(**kwargs))
    except Exception as e:
        log.warning("schedule_record_http_request failed: %s", e)


async def record_llm_call(
    *,
    trace_id: str | None = None,
    call_kind: str,
    model_source: str,
    model: str,
    duration_ms: float,
    messages: list[Any],
    tools: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
    model_tag: str | None = None,
) -> None:
    """Persist one LLM audit row aligned with ``llm_debug_log`` field shapes."""
    if not is_audit_enabled():
        return
    try:
        msg_json = _serialize_json(prepare_messages_for_log(messages))
        tools_json = None
        if tools is not None:
            tools_json = _serialize_json(tool_names_from_spec(tools))
        await _insert_llm_row(
            trace_id=(trace_id or get_trace_id() or "-"),
            project_id=project_id,
            session_id=session_id,
            call_kind=call_kind,
            model_source=model_source,
            model=model,
            model_tag=model_tag,
            messages=msg_json,
            tools=tools_json,
            result=_serialize_json(result),
            error=_serialize_json(error),
            duration_ms=round(duration_ms, 2),
            created_at=_utc_now_iso(),
        )
    except Exception as e:
        log.warning("record_llm_call failed: %s", e)


def schedule_record_llm_call(**kwargs: Any) -> None:
    """Fire-and-forget LLM audit write; never raises."""
    if not is_audit_enabled():
        return
    try:
        _schedule(record_llm_call(**kwargs))
    except Exception as e:
        log.warning("schedule_record_llm_call failed: %s", e)


def parse_request_body_for_audit(
    body_bytes: bytes,
    *,
    path: str,
    content_type: str | None,
) -> Any:
    """Decode request body per route policy (JSON, metadata-only, or skip)."""
    kind = infer_request_kind(path)
    if kind == "health":
        return None
    if kind == "import":
        return {"_audit": "multipart_metadata_only"}
    if not body_bytes:
        return None
    ct = (content_type or "").lower()
    if "application/json" in ct or not ct:
        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
            if kind == "session_sync":
                from app.services.session_store import redact_session_body_for_audit

                return redact_session_body_for_audit(parsed)
            if kind == "data_upload" and isinstance(parsed, dict):
                schema = parsed.get("schema")
                rows = parsed.get("rows")
                return {
                    "name": parsed.get("name"),
                    "rowCount": len(rows) if isinstance(rows, list) else 0,
                    "schemaCols": len(schema) if isinstance(schema, list) else 0,
                    "_audit": "data_upload_metadata_only",
                }
            return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _truncate_text(body_bytes.decode("utf-8", errors="replace"), _max_body_chars())
    return _truncate_text(
        f"<non-json body len={len(body_bytes)} content-type={content_type}>",
        _max_body_chars(),
    )


def parse_response_body_for_audit(
    body_bytes: bytes,
    *,
    path: str,
    content_type: str | None,
    is_streaming: bool,
) -> Any:
    if is_streaming:
        return {"response_kind": "sse"}
    kind = infer_request_kind(path)
    if kind == "export":
        return {
            "_audit": "binary_response",
            "content_type": content_type,
            "byte_length": len(body_bytes),
        }
    if kind == "health" and not body_bytes:
        return None
    if not body_bytes:
        return None
    ct = (content_type or "").lower()
    if "application/json" in ct or not ct:
        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
            if kind == "session_sync" and isinstance(parsed, dict):
                return {
                    "_audit": "session_sync_metadata_only",
                    "sessionId": parsed.get("sessionId"),
                    "version": parsed.get("version"),
                    "updatedAt": parsed.get("updatedAt"),
                }
            return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _truncate_text(body_bytes.decode("utf-8", errors="replace"), _max_body_chars())
    return _truncate_text(
        f"<non-json response len={len(body_bytes)} content-type={content_type}>",
        _max_body_chars(),
    )
