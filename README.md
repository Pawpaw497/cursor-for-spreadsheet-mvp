# Cursor for Spreadsheet

> English primary README · 中文: [README.cn.md](README.cn.md)

> **TL;DR** — Press **Cmd+K** in the browser spreadsheet, describe what to change in natural language; **LangGraph Agent multi-turn tool calls** (`get_schema` / `get_sample_rows` / `validate_expression`) → clarification when intent is ambiguous → structured JSON **Plan** → **Diff preview** (column highlights) → **Apply** with **undo**. Supports single-table and multi-table flows (join / lookup, etc.) and SSE streaming. A presentable personal project for exploring modern agent patterns — not a commercial product.

## Project intent

A **presentable, evolvable personal project** for applying the latest **agent technology, strategy, and philosophy** to spreadsheet editing: multi-turn tool loops, clarification before action, structured plans with preview/undo, and workspace memory. The codebase is meant to be shown, forked, and extended — not a frozen prototype.

> **Why not output CSV directly?** — Structured Plans make every step dry-runnable, previewable, and undoable; the Agent triggers Clarification instead of guessing when intent is unclear — `Structured Plan + Agent clarification + interpretable Diff + undo` vs `opaque chat-to-CSV`.

![Workflow: Cmd+K → Plan → Diff → Apply](docs/assets/demo-flow.svg)

## Project declaration & license

- **Nature**: Personal open-source project — a living sandbox for agent techniques on spreadsheets, not a commercial product; no production warranty or long-term maintenance commitment.
- **Forking**: Forks, learning, and derivative work are welcome; you must **keep attribution and credit the source** (see [`LICENSE`](LICENSE)).
- **License**: [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — non-commercial use, modification, and redistribution allowed; **commercial use prohibited** (including direct/indirect profit or internal production use); commercial licensing requires written permission from the author.
- **Security**: Local-first, not production-hardened; `add_column` expressions run in the browser via `new Function`; chat and spreadsheet context may be stored in plaintext locally — **do not upload sensitive data or use on shared devices**.

## Overview

- **Cmd+K workflow**: Natural language + table schema / sample rows → LLM generates JSON plan → in-grid Diff preview → Apply / undo.
- **Single-table** (`/api/plan`) and **multi-table projects** (`/api/plan-project`): column/row transforms, join, lookup, aggregation, etc.; shared Plan contract across frontend and backend.
- **Agent mode**: `/api/agent`, `/api/agent-stream` — multi-turn tool calls; per-table semantic profiling (topic/description/granularity); clarification before plan generation when ambiguous.
- **Stack**: React 18 + Vite + AG Grid; FastAPI + uv; **LangGraph · Pydantic AI**; OpenRouter (`deepseek/deepseek-v4-pro`) + local Ollama; SQLite request and LLM call audit.
- **Documentation**: feature deep dive [`docs/features.md`](docs/features.md); technical index [`docs/README.md`](docs/README.md); Chinese README [`README.cn.md`](README.cn.md).

## Single-model phase (current)

During the current development phase, the cloud path is pinned to **one** model — **`deepseek/deepseek-v4-pro`** via OpenRouter — so engineering effort isn't spent chasing weak-model fallbacks, OpenRouter auto/free routing, or user-defined model IDs this early. The UI does not expose cloud model switching.

Local Ollama support is kept as-is by design, not deprecated: it exists for confidentiality-sensitive use where data must stay on-machine. It's simply not the current development focus, so the UI doesn't expose local model selection either — use it via the API (`modelSource: "local"`) or see below.

## Quick start

**Full setup** (OpenRouter, full `.env` table, first Cmd+K): [`docs/getting-started.md`](docs/getting-started.md)

### Recommended: OpenRouter + DeepSeek V4 Pro

1. **Prerequisites**: Python 3.10+, [uv](https://docs.astral.sh/uv/), Node.js 18+, an [OpenRouter](https://openrouter.ai) API key.

```bash
git clone https://github.com/Pawpaw497/cursor-for-spreadsheet.git
cd cursor-for-spreadsheet
```

2. **Backend** (use `server/.venv` from `uv sync` only — not a root-level venv):

```bash
cd server
cp .env.example .env   # set OPENROUTER_API_KEY
uv sync
uv run uvicorn main:app --reload --port 8787
```

3. **Frontend** (new terminal):

```bash
cd client
npm install
npm run dev
```

4. Open **http://localhost:5173**, load a sample table, then **Cmd+K** → enter a prompt (e.g. `Add amount column = quantity * unit price on sales orders`) → **Generate Plan** → **Apply**. Sample prompts: [`test-data/test-prompts.md`](test-data/test-prompts.md).

### Local: Ollama (privacy / offline use)

The backend still fully supports local Ollama (`modelSource: local`) for cases where data must stay on-machine; the UI just doesn't expose model selection while the cloud path is single-model. See [`docs/getting-started.md`](docs/getting-started.md) Path B if you need a keyless local path.

### One-command start (optional)

```bash
make dev    # API (8787) + Vite (5173) in background; stop processes manually when done
```

---

## Development & testing

```bash
make test           # backend pytest + frontend Vitest
make test-server    # cd server && uv sync && uv run pytest -q
make test-client    # cd client && npm ci && npm test
```

Or per directory (matches CI):

```bash
cd server && uv sync && uv run pytest -q
cd client && npm ci && npm test
```

Optional cloud E2E (requires API key; not run in CI): `RUN_CLOUD_LLM_E2E=1 uv run pytest tests/test_cloud_llm_sample_e2e.py -m integration -q` (from `server/`).

Agent quality eval suite (opt-in, calls a real LLM, not run in CI): `uv run python -m evaluation` (from `server/`) — see [`docs/evaluation.md`](docs/evaluation.md).

Logging and SQLite audit: [`docs/logging-and-debug.md`](docs/logging-and-debug.md)

---

## More documentation

| Topic | Location |
|-------|----------|
| License & forking | This README § Project declaration & license, [`LICENSE`](LICENSE), [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Full setup & first run | [`docs/getting-started.md`](docs/getting-started.md) |
| Features (Agent clarification, preview lifecycle, etc.) | [`docs/features.md`](docs/features.md) |
| Chinese README | [`README.cn.md`](README.cn.md) |
| Technical reference | [`docs/README.md`](docs/README.md) |
| Dev & test | This README § Development & testing, root [`Makefile`](Makefile) |
| Workflow diagram | [`docs/assets/demo-flow.svg`](docs/assets/demo-flow.svg) |
| Cursor AI visibility | [`.cursorignore`](.cursorignore), [`.cursorindexingignore`](.cursorindexingignore) |
| Local private notes (not committed) | `docs/local/` (see [`.gitignore`](.gitignore)) |

Canonical docs under `docs/` ship with the repo; `scripts/`, `docs/local/`, and **`.cursor/`** are not committed by default (see [`.gitignore`](.gitignore)). **`.cursor/`** is the maintainer's local Cursor workspace (rules / plans / skills) and is **not** included in public clones.
