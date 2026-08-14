"""SQLite audit logging: DB writes, middleware, PA hook, privacy, tolerance."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config as config_mod
from app.agent.pa_decision import PaTurnResult, pa_decision_step
from app.config import settings
from app.logging_config import reset_trace_id, set_trace_id
from app.main import app
from app.models.agent_models import AgentState, TableContext
from app.models.plan import Plan
from app.services import audit_db
from app.services import audit_log as audit_mod


async def _flush_audit_tasks() -> None:
    """Wait for scheduled audit writes to finish, deterministically.

    A fixed sleep raced with ``_insert_with_retry``: its first backoff alone is 50ms
    (``min(0.05 * 2**attempt, 1.0)``), so any lock contention on a loaded CI runner
    blew past a 50ms window and the row was not yet visible when the assertion ran.
    """
    await audit_mod.drain_background_tasks(timeout=30.0)


def _schedule_audit_sync(**kwargs: object) -> None:
    """Test helper: complete HTTP audit write before middleware returns (avoids CI races)."""

    def _run() -> None:
        asyncio.run(audit_mod.record_http_request(**kwargs))

    thread = __import__("threading").Thread(target=_run)
    thread.start()
    # /api/agent writes are heavier than the old /api/plan; slow CI runners
    # exceeded 5s (2026-07-20 post-outage backlog), so allow a generous margin.
    thread.join(timeout=30.0)
    assert not thread.is_alive(), "audit HTTP write timed out"


async def _http_rows(trace_id: str) -> list[audit_db.HttpRequestLog]:
    factory = audit_db.get_session_factory()
    assert factory is not None
    async with factory() as session:
        result = await session.execute(
            select(audit_db.HttpRequestLog).where(
                audit_db.HttpRequestLog.trace_id == trace_id
            )
        )
        return list(result.scalars().all())


async def _llm_rows(trace_id: str | None = None) -> list[audit_db.LlmCallLog]:
    factory = audit_db.get_session_factory()
    assert factory is not None
    async with factory() as session:
        stmt = select(audit_db.LlmCallLog)
        if trace_id:
            stmt = stmt.where(audit_db.LlmCallLog.trace_id == trace_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


@pytest.fixture
def audit_db_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.sqlite3"


@pytest.fixture
def enable_audit(monkeypatch: pytest.MonkeyPatch, audit_db_path: Path) -> Path:
    for mod in (config_mod, audit_mod, audit_db):
        monkeypatch.setattr(mod.settings, "AUDIT_DB_ENABLED", True)
        monkeypatch.setattr(mod.settings, "AUDIT_DB_PATH", str(audit_db_path))
    return audit_db_path


@pytest.fixture
def audit_initialized(enable_audit: Path):
    async def setup_teardown():
        await audit_db.reset_audit_db_for_tests()
        await audit_db.init_audit_db()
        yield
        await audit_db.close_audit_db()
        await audit_db.reset_audit_db_for_tests()

    gen = setup_teardown()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(gen.__anext__())
        yield enable_audit
    finally:
        try:
            loop.run_until_complete(gen.__anext__())
        except StopAsyncIteration:
            pass
        loop.close()


def test_record_http_and_llm_roundtrip(audit_initialized: Path) -> None:
    async def run() -> None:
        token = set_trace_id("trace-db-unit")
        try:
            await audit_mod.record_http_request(
                trace_id="trace-db-unit",
                method="POST",
                path="/api/agent",
                request_body={"prompt": "hi"},
                response_status=200,
                response_body={"ok": True},
                duration_ms=12.5,
                request_kind="agent",
            )
            await audit_mod.record_llm_call(
                trace_id="trace-db-unit",
                call_kind="plain",
                model_source="cloud",
                model="test/model",
                duration_ms=50.0,
                messages=[{"role": "user", "content": "hello"}],
                result={"content": "ok"},
            )
        finally:
            reset_trace_id(token)

        http = await _http_rows("trace-db-unit")
        llm = await _llm_rows("trace-db-unit")
        assert len(http) == 1
        assert http[0].response_status == 200
        assert json.loads(http[0].request_body or "{}")["prompt"] == "hi"
        assert len(llm) == 1
        assert llm[0].call_kind == "plain"
        assert llm[0].model == "test/model"

    asyncio.run(run())


def test_audit_engine_configured_for_concurrent_writes(
    audit_initialized: Path,
) -> None:
    """Fast guard on the engine settings the concurrency fixes depend on."""
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    async def run() -> None:
        engine = audit_db._engine
        assert engine is not None
        assert isinstance(engine.pool, NullPool), (
            "audit engine must not pool connections across event loops"
        )
        async with engine.connect() as conn:
            journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        assert str(journal).lower() == "wal", f"journal_mode={journal!r}, want wal"
        assert busy == audit_db._SQLITE_BUSY_TIMEOUT_MS, (
            f"busy_timeout={busy!r}, want {audit_db._SQLITE_BUSY_TIMEOUT_MS} "
            "(sqlite3 default 5000 is too low for loaded CI runners)"
        )

    asyncio.run(run())


def test_concurrent_http_and_llm_writes_separate_event_loops(
    audit_initialized: Path,
) -> None:
    """Two event loops writing the same audit file must not silently drop rows.

    Mirrors production: one ``/api/agent`` request writes an HTTP audit row from a
    background thread (``_schedule`` has no running loop -> ``asyncio.run``) while the
    ``pa_turn`` LLM audit row is written on the app loop. Both share the global engine
    and land in the same SQLite file. ``record_*`` swallows failures with a WARNING, so
    contention shows up as *missing rows*, never as an exception -- hence the assertion
    is a row count.

    Scope note: this is an *invariant guard*, not a reproducer. It passes against the
    pre-fix engine config too, because sqlite3's default 5s busy timeout absorbs the
    contention on a fast disk (it does catch cross-event-loop connection reuse, which
    would raise "Future attached to a different loop"). The deterministic reproducer
    for the observed CI failure is
    ``test_audit_write_survives_write_lock_held_past_default_budget`` below.
    """
    import threading

    trace = "trace-concurrent-write"
    rows_per_side = 25
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    async def _write_http() -> None:
        for i in range(rows_per_side):
            await audit_mod.record_http_request(
                trace_id=trace,
                method="POST",
                path="/api/agent",
                request_body={"prompt": f"concurrent-{i}"},
                response_status=200,
                duration_ms=1.0,
                request_kind="agent",
            )

    async def _write_llm() -> None:
        for i in range(rows_per_side):
            await audit_mod.record_llm_call(
                trace_id=trace,
                call_kind="pa_turn",
                model_source="cloud",
                model="test/model",
                duration_ms=1.0,
                messages=[{"role": "user", "content": f"concurrent-{i}"}],
            )

    def _run(coro_factory) -> None:
        try:
            barrier.wait(timeout=10.0)
            asyncio.run(coro_factory())
        except BaseException as e:  # surfaced after join for a readable failure
            errors.append(e)

    threads = [
        threading.Thread(target=_run, args=(_write_http,)),
        threading.Thread(target=_run, args=(_write_llm,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60.0)
        assert not t.is_alive(), "concurrent audit write timed out"

    assert not errors, f"audit writer thread raised: {errors!r}"

    async def check() -> None:
        http = await _http_rows(trace)
        llm = await _llm_rows(trace)
        assert len(http) == rows_per_side, (
            f"HTTP audit rows dropped under concurrency: "
            f"{len(http)}/{rows_per_side} persisted"
        )
        assert len(llm) == rows_per_side, (
            f"LLM audit rows dropped under concurrency: "
            f"{len(llm)}/{rows_per_side} persisted"
        )

    asyncio.run(check())


def test_audit_write_survives_write_lock_held_past_default_budget(
    audit_initialized: Path,
) -> None:
    """An audit row must survive a write lock held longer than sqlite3's 5s default.

    Deterministic reproducer for the PR #39 CI failure. sqlite3.connect defaults to
    ``timeout=5.0`` (busy_timeout=5000), so the pre-fix engine tolerates only ~5s of
    contention; on a loaded CI runner the DELETE-journal write window exceeded that and
    ``record_http_request`` swallowed ``database is locked`` into a WARNING, dropping
    the row while the business request still returned 200.

    Holding an EXCLUSIVE write lock for ``hold_s`` (> 5s default, < 30s configured)
    pins that behavioural difference: pre-fix the row is silently dropped, post-fix the
    write waits out the lock and persists.
    """
    import sqlite3
    import threading
    import time

    hold_s = 6.5
    trace = "trace-lock-held"
    lock_acquired = threading.Event()
    holder_error: list[BaseException] = []

    def _hold_write_lock() -> None:
        try:
            conn = sqlite3.connect(str(audit_initialized), timeout=1.0)
            try:
                conn.execute("BEGIN EXCLUSIVE")
                lock_acquired.set()
                time.sleep(hold_s)
                conn.rollback()
            finally:
                conn.close()
        except BaseException as e:
            holder_error.append(e)
            lock_acquired.set()

    holder = threading.Thread(target=_hold_write_lock)
    holder.start()
    try:
        assert lock_acquired.wait(timeout=10.0), "could not acquire exclusive lock"
        assert not holder_error, f"lock holder failed: {holder_error!r}"

        async def run() -> None:
            await audit_mod.record_http_request(
                trace_id=trace,
                method="POST",
                path="/api/agent",
                response_status=200,
                duration_ms=1.0,
                request_kind="agent",
            )

        asyncio.run(run())
    finally:
        holder.join(timeout=30.0)
        assert not holder.is_alive(), "lock holder thread did not finish"

    async def check() -> None:
        rows = await _http_rows(trace)
        assert len(rows) == 1, (
            "audit row silently dropped while a write lock was held past the default "
            f"5s busy timeout: {len(rows)}/1 persisted"
        )

    asyncio.run(check())


def test_schedule_keeps_strong_reference_until_task_finishes() -> None:
    """``loop.create_task`` only weak-refs the task; audit rows must not be GC-able."""
    import gc

    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[str] = []

    async def slow_write() -> None:
        started.set()
        await release.wait()
        finished.append("done")

    async def run() -> None:
        audit_mod._schedule(slow_write())
        await started.wait()
        gc.collect()
        assert audit_mod._background_tasks, (
            "no strong reference held for an in-flight audit write"
        )
        release.set()
        await audit_mod.drain_background_tasks(timeout=5.0)
        assert finished == ["done"]
        assert not audit_mod._background_tasks, "completed task not discarded"

    asyncio.run(run())


def test_drain_background_tasks_swallows_write_failures() -> None:
    """Draining must preserve fire-and-forget: a failed audit write never propagates."""

    async def boom() -> None:
        raise OSError("audit write failed")

    async def run() -> None:
        audit_mod._schedule(boom())
        await audit_mod.drain_background_tasks(timeout=5.0)
        assert not audit_mod._background_tasks

    asyncio.run(run())


def test_drain_background_tasks_ignores_tasks_from_other_loops() -> None:
    """A leftover task from a closed loop must not make a later drain hang or raise."""
    stale = asyncio.new_event_loop()
    try:
        async def _park() -> None:
            await asyncio.get_running_loop().create_future()

        task = stale.create_task(_park())
        stale.run_until_complete(asyncio.sleep(0))  # let the coroutine start
        audit_mod._background_tasks.add(task)

        async def run() -> None:
            await audit_mod.drain_background_tasks(timeout=1.0)

        asyncio.run(run())
    finally:
        audit_mod._background_tasks.discard(task)
        task.cancel()
        stale.run_until_complete(asyncio.gather(task, return_exceptions=True))
        stale.close()


def test_workspace_key_stored_as_hash_only() -> None:
    raw = "workspace:file:abc123"
    hashed = audit_mod.workspace_key_hash(raw)
    assert hashed != raw
    assert len(hashed) == 64


def test_audit_log_module_has_no_memory_writes() -> None:
    path = Path(audit_mod.__file__)
    text = path.read_text(encoding="utf-8")
    assert "WorkspaceMemory" not in text
    assert "memory_compaction" not in text
    assert "AgentSessionMemory" not in text


def test_middleware_records_agent_request(
    audit_initialized: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "k")
    plan = Plan.model_validate(
        {
            "intent": "x",
            "steps": [{"action": "add_column", "name": "c", "expression": "1"}],
        }
    )
    turn = PaTurnResult(tool_parts=[], text="", structured_plan=plan)

    monkeypatch.setattr("app.main.schedule_record_http_request", _schedule_audit_sync)
    # 本测试只验证中间件 HTTP 审计；关掉 pa_turn 的 LLM 审计写，
    # 避免它与同步写线程在同一 SQLite 文件上并发导致 database-is-locked（CI 慢盘可复现）。
    monkeypatch.setattr(
        "app.agent.pa_decision._schedule_pa_turn_audit", lambda **_kwargs: None
    )

    body = {
        "prompt": "add column",
        "tables": [
            {
                "name": "Sheet1",
                "schema": [{"key": "a", "type": "string"}],
            }
        ],
        "modelSource": "cloud",
        "previewLifecycle": False,
    }
    with patch(
        "app.agent.pa_decision._run_pa_single_turn",
        new=AsyncMock(return_value=turn),
    ):
        with TestClient(app) as client:
            resp = client.post(
                "/api/agent",
                json=body,
                headers={"X-Request-ID": "trace-mw-agent"},
            )
            assert resp.status_code == 200

    async def check() -> None:
        # App lifespan may close the engine after TestClient exits; reconnect to the same DB file.
        await audit_db.init_audit_db()
        rows = await _http_rows("trace-mw-agent")
        assert len(rows) >= 1
        row = rows[0]
        assert row.method == "POST"
        assert row.path == "/api/agent"
        assert row.request_kind == "agent"
        assert row.response_status == 200

    asyncio.run(check())


def test_request_ok_when_audit_insert_fails(
    monkeypatch: pytest.MonkeyPatch, enable_audit: Path
) -> None:
    async def boom(**kwargs: object) -> None:
        raise OSError("audit write failed")

    monkeypatch.setattr(audit_mod, "_insert_http_row", boom)
    monkeypatch.setattr(audit_mod.settings, "AUDIT_DB_ENABLED", True)

    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200


def test_pa_turn_writes_llm_log(
    audit_initialized: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "k")
    plan = Plan.model_validate(
        {"intent": "x", "steps": [{"action": "add_column", "name": "c", "expression": "1"}]}
    )
    state = AgentState(
        tables=[
            TableContext(
                name="Sheet1",
                schema=[{"key": "a", "type": "string"}],
            )
        ],
        messages=[],
        user_prompt="Add column",
        model_source="cloud",
    )
    turn = PaTurnResult(tool_parts=[], text="", structured_plan=plan)

    async def run() -> None:
        token = set_trace_id("trace-pa-turn")
        try:
            with patch(
                "app.agent.pa_decision._run_pa_single_turn",
                new=AsyncMock(return_value=turn),
            ):
                await pa_decision_step(state, use_tools=True)
            await _flush_audit_tasks()
            rows = await _llm_rows("trace-pa-turn")
            assert any(r.call_kind == "pa_turn" for r in rows)
        finally:
            reset_trace_id(token)

    asyncio.run(run())


def test_agent_body_audited_without_memory_side_effects(
    audit_initialized: Path,
) -> None:
    """Large memory-shaped fields may be stored in audit only — no memory module coupling."""

    async def run() -> None:
        await audit_mod.record_http_request(
            trace_id="trace-agent-mem",
            method="POST",
            path="/api/agent",
            request_body={
                "appliedPlansSummary": "prior plan summary",
                "previewHistory": [{"id": "p1", "status": "pending"}],
            },
            response_status=200,
            duration_ms=1.0,
            request_kind="agent",
        )
        rows = await _http_rows("trace-agent-mem")
        assert len(rows) == 1
        body = json.loads(rows[0].request_body or "{}")
        assert "appliedPlansSummary" in body

    asyncio.run(run())


def test_log_llm_call_schedules_db_even_without_ndjson(
    audit_initialized: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import llm_debug_log as debug_mod

    monkeypatch.setattr(debug_mod.settings, "LLM_DEBUG_LOG_DIR", "")
    token = set_trace_id("trace-llm-dual")
    try:
        debug_mod.log_llm_call(
            call="plain",
            model_source="cloud",
            model="m",
            duration_ms=1.0,
            messages=[{"role": "user", "content": "x"}],
            result={"content": "y"},
        )
    finally:
        reset_trace_id(token)

    async def check() -> None:
        await _flush_audit_tasks()
        for _ in range(20):
            rows = await _llm_rows("trace-llm-dual")
            if rows:
                break
            await asyncio.sleep(0.05)
        assert len(rows) == 1

    asyncio.run(check())
