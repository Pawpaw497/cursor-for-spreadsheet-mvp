---
name: run-project
description: Runs the full spreadsheet-cursor-mvp stack (FastAPI on 8787 + Vite on 5173), including first-time .env and dependency setup. Use when the user asks to run the project, start dev, bring up the app end-to-end, or verify API + UI together.
---

# Run project (full stack)

## What runs where

| Service | Path | Default URL | Command |
|--------|------|-------------|---------|
| API | `server/` | `http://localhost:8787` | `uv run uvicorn main:app --reload --port 8787` |
| Web | `client/` | `http://localhost:5173` | `npm run dev` |

## Prerequisites (machine)

- Python 3.10+ and **[uv](https://docs.astral.sh/uv/)** (recommended for `server/`)
- Node.js 18+
- For LLM calls: either configure **OpenRouter** in `server/.env`, or run **Ollama** (default `http://localhost:11434`; see `server/.env` / `server/app/config.py`). Optional: `ollama serve` and a pulled model (e.g. `qwen2.5:7b` per repo docs).

## First-time backend config

1. `cd server`
2. `cp .env.example .env` if `.env` is missing.
3. Edit `.env` for `OPENROUTER_API_KEY` and/or Ollama settings as needed.
4. `uv sync` (creates `server/.venv` and installs deps from `uv.lock`).

## Start backend (long-running)

From `server/`:

```bash
uv run uvicorn main:app --reload --port 8787
```

Run in the **background**; check logs. Verify: `http://localhost:8787/api/config` or `http://localhost:8787/docs`.

## Start frontend (long-running)

From `client/`:

```bash
npm install   # if node_modules missing
npm run dev
```

Run in the **background**; default Vite port **5173** (see `client/vite.config.ts`).

## Full-stack verification

- Backend up: `/api/config` or `/docs` on port **8787**
- App in browser: **5173**; first load may call `/api/load-sample` — backend should be up first
- If only the UI is needed with mocks, the user can still use the client alone; for AI features, both processes must be healthy

## Agent notes

- Check the terminals state before spawning duplicate `uvicorn` or `npm run dev`.
- Two terminals (or two background jobs) are normal: one for `server/`, one for `client/`.
- For **frontend only**, the narrower workflow is in `.cursor/skills/run-client/SKILL.md`.
- Always use `server/.venv` via `uv` for backend work; do not use repo-root `env/`, `.venv`, or `venv` as this project's standard runtime.
