---
name: test-server
description: >-
  Runs general backend checks for spreadsheet-cursor-mvp (uv sync, import app, HTTP
  smoke against /health and /api/config). Use when the user asks to test the
  server, verify the API, run backend smoke tests, or validate FastAPI after
  changes; when debugging server startup; or when CI needs a quick server
  health script.
---

# Test server (spreadsheet-cursor-mvp)

## Scope

- **In scope**: dependency install, one-shot `import app` smoke, optional live HTTP checks on canonical ports, optional `TestClient` snippet when adding automated tests.
- **Not in scope**: full LLM integration tests (need keys and external services); E2E with the Vite client (use [run-server](../run-server/SKILL.md) or [run-project](../run-project/SKILL.md)).

## Environment

- Work from **`server/`** only for backend; use **`uv run`** and **`server/.venv`** (see repository workspace rules). Do not use repo-root `env/`, `.venv`, or `venv` as the standard env.

## One-shot checks (no server process)

1. **Sync dependencies**

   ```bash
   cd server && uv sync
   ```

2. **Import smoke** (confirms `app.main:app` loads; catches many syntax/import errors before bind)

   ```bash
   cd server && uv run python -c "from app.main import app; print('ok')"
   ```

## Live HTTP checks (server must be running)

Start API (from `server/`, default port in docs):

```bash
cd server && uv run uvicorn main:app --reload --port 8787
```

In another shell:

| Check | Command |
|--------|---------|
| Health | `curl -sS http://127.0.0.1:8787/health` → expect `{"ok":true}` |
| Config | `curl -sS http://127.0.0.1:8787/api/config` → JSON with model fields |
| OpenAPI | open `http://127.0.0.1:8787/docs` in browser or `curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/docs` → `200` |

If port conflicts, align **both** the uvicorn `--port` and any client `API_BASE` (see [run-server](../run-server/SKILL.md)).

## Optional: in-process API tests (pytest)

`pyproject.toml` may not list `pytest` yet. To add **FastAPI** route tests without a long‑running server:

1. Add dev dependencies (example): `httpx` (often already a runtime dep) and `pytest` via `uv add --dev pytest` in `server/`.
2. Use Starlette’s `TestClient` against `app` from `app.main`:

   ```python
   from fastapi.testclient import TestClient
   from app.main import app

   client = TestClient(app)
   def test_health():
       r = client.get("/health")
       assert r.status_code == 200
       assert r.json() == {"ok": True}
   ```

3. Run: `cd server && uv run pytest -q`

Keep tests **offline** by default: mock LLM/HTTP to OpenRouter in tests; avoid requiring live keys in CI.

## Report back

After running checks, state: import result, HTTP status or errors, and any warnings printed during import (e.g. Pydantic shadowing) if relevant to the change under test.
