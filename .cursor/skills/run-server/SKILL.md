---
name: run-server
description: Starts the spreadsheet-cursor-mvp dev stack (FastAPI on 8787, Vite on 5173), verifies health endpoints, and notes LLM prerequisites. Use when the user asks to run, start, or restart the server, backend, frontend, dev environment, or local demo; when debugging connection errors; or when an agent needs the canonical run commands for this repository.
---

# Run dev servers (spreadsheet-cursor-mvp)

## Prerequisites

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/)** for `server/`
- **Node.js 18+** for `client/`
- **LLM**: OpenRouter key in `server/.env` and/or **Ollama** on `http://localhost:11434` (see repo `README.md`)
- Backend runtime convention: use `server/.venv` managed by `uv`; do not treat repo-root `env/`, `.venv`, or `venv` as the standard environment.

## One-time setup

1. **Backend env** (from repo root):

   ```bash
   cd server && cp .env.example .env
   ```

   Edit `server/.env` for `OPENROUTER_API_KEY`, Ollama settings, etc.

2. **Install deps**:

   ```bash
   cd server && uv sync
   cd ../client && npm install
   ```

## Run (two terminals)

**Terminal A — API**

```bash
cd server
uv run uvicorn main:app --reload --port 8787
```

- Health: `http://localhost:8787/api/config` or `http://localhost:8787/docs`

**Terminal B — frontend**

```bash
cd client
npm run dev
```

- App: `http://localhost:5173` (Vite default in `client/vite.config.ts`)
- Frontend calls API at **`http://localhost:8787`** (hardcoded in `client/src/llm.ts`); backend must be up first for sample load and AI flows.

## Common issues

| Symptom | Check |
|--------|--------|
| Sample load / API errors in UI | Backend running on **8787**; visit `/api/config` |
| Cloud model 400 | `OPENROUTER_API_KEY` missing in `server/.env` |
| Ollama 503 / unreachable | `ollama serve`, model pulled; VPN/proxy on `localhost:11434` |
| Port in use | Change uvicorn `--port` **and** update `API_BASE` in `client/src/llm.ts` (and any other references) for consistency |

## Optional: Ollama

```bash
ollama serve
ollama pull qwen2.5:7b
```

Or set `AUTO_START_OLLAMA` in `server/.env` if using that feature (still requires Ollama installed).
