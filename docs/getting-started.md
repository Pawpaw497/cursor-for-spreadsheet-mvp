# Getting started (detailed)

Step-by-step setup beyond the [root README Quick Start](../README.md). Workflow overview: [demo-flow.svg](./assets/demo-flow.svg). For architecture and APIs see [architecture.md](./architecture.md).

Optional one-liner from repo root: `make dev` (background API + Vite; stop processes manually).

## Prerequisites

| Tool | Check | Notes |
|------|-------|-------|
| Python 3.10+ | `python3 --version` | Backend uses `uv` in `server/` |
| [uv](https://docs.astral.sh/uv/) | `uv --version` | Creates `server/.venv` via `uv sync` |
| Node.js 18+ | `node -v` | Frontend in `client/` |
| OpenRouter API key | [openrouter.ai](https://openrouter.ai) | **Recommended** product path |

**Environment convention:** backend runtime is **`server/.venv` only** (from `uv sync`). Do not use repo-root `env/`, `.venv`, or `venv` for this project.

If your default `python` is Conda and you want an isolated backend env:

```bash
cd server
uv python pin 3.11   # or set UV_PYTHON
uv sync
```

## Path A — OpenRouter + DeepSeek V4 Pro (recommended)

The product currently uses a **single** cloud model: `deepseek/deepseek-v4-pro`. The UI does not expose model switching.

1. Create an API key at [openrouter.ai](https://openrouter.ai).
2. `cd server && cp .env.example .env` and set:

```env
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=deepseek/deepseek-v4-pro
```

Defaults in `.env.example` already point at this model; you only need a valid key.
3. `uv sync && uv run uvicorn main:app --reload --port 8787`
4. `cd client && npm install && npm run dev` → `http://localhost:5173`

**Advanced:** `OPENROUTER_MODELS` / `OPENROUTER_LABELS` still parse comma-separated lists for developer overrides, but the UI no longer shows a dropdown. Changing models via env is outside the current product scope.

**Optional cloud E2E** (real API calls, needs key):

```bash
cd server
RUN_CLOUD_LLM_E2E=1 E2E_CLOUD_MODEL_ID=deepseek/deepseek-v4-pro \
  uv run pytest tests/test_cloud_llm_sample_e2e.py -q
```

## Path B — Ollama (local)

> Backend code remains fully supported; the UI just doesn't expose model selection while the cloud path is single-model.

See README § Local: Ollama. Summary:

1. `ollama serve` and `ollama pull qwen2.5:7b`
2. `cd server && cp .env.example .env` — `OPENROUTER_API_KEY` can stay empty; `AUTO_START_OLLAMA=1` in `.env.example` tries to start Ollama with the API.
3. `uv sync && uv run uvicorn main:app --reload --port 8787`
4. `cd client && npm install && npm run dev` → `http://localhost:5173`

Default Ollama settings (from `server/.env.example` / `server/app/config.py`):

| Variable | Default |
|----------|---------|
| `OLLAMA_BASE` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b` |
| `OLLAMA_MODELS` | `qwen2.5:7b` |
| `AUTO_START_OLLAMA` | `1` in `.env.example` (`0` in code default if unset) |

If Ollama calls fail with 503, disable VPN or add `localhost:11434` to the proxy bypass list. API requests must still send `modelSource: "local"` manually (e.g. via API client); the web UI always uses cloud + v4-pro.

## Backend `.env` reference

Copy from `server/.env.example`. Common keys:

| Key | Purpose |
|-----|---------|
| `OPENROUTER_API_KEY` | Cloud LLM (required for default product path) |
| `OPENROUTER_MODEL` | Default cloud model id (`deepseek/deepseek-v4-pro`) |
| `OPENROUTER_MODELS` / `OPENROUTER_LABELS` | Parsed model list (single entry by default; UI hidden) |
| `OLLAMA_BASE` / `OLLAMA_MODEL` / `OLLAMA_MODELS` / `OLLAMA_LABELS` | Local models (developer path) |
| `AUTO_START_OLLAMA` | `1` to spawn `ollama serve` on API startup |
| `AGENT_TRANSCRIPTS_DIR` | Optional JSONL agent transcripts |
| `AUDIT_DB_ENABLED` | SQLite HTTP/LLM audit (default on) — see [logging-and-debug.md](./logging-and-debug.md) |
| `SESSION_MEMORY_DB_ENABLED` | Optional server session backup — see [agent-memory.md](./agent-memory.md) |

Verify API: `http://localhost:8787/api/config` or `/docs`.

- Missing `OPENROUTER_API_KEY` when cloud is selected → `[400]` from `/api/plan`.
- Invalid key → `[502]`; UI shows a Chinese auth-failure hint.

`/api/config` returns `llmClientTimeoutRecommendedMs` aligned with backend upstream HTTP timeouts.

## First Cmd+K session

1. Ensure backend and frontend are up; status bar shows no backend error.
2. Sample data loads from `/api/load-sample` (`test-data/sample.xlsx`). Use toolbar **加载示例** if load failed.
3. **Cmd+K** — opens AI panel and focuses the prompt.
4. Try prompts aligned with sample column names — see [test-data/test-prompts.md](../test-data/test-prompts.md).
5. **Generate Plan** → diff highlights + Diff Preview bar.
6. **Apply** → backend executes plan; **撤销** restores pre-apply snapshot.

Import Excel/CSV via toolbar; imports time out after ~20 s with a visible message.

## Feature deep dives

| Topic | Doc |
|-------|-----|
| Features | [features.md](./features.md) |
| Agent clarification | [agent-memory.md § Clarification and agent transcript](./agent-memory.md#clarification-and-agent-transcript), `server/app/agent/clarification.py` |
| Agent preview lifecycle | [agent-preview-lifecycle.md](./agent-preview-lifecycle.md) |
| Browser storage | [client-storage.md](./client-storage.md) |
| Agent memory | [agent-memory.md](./agent-memory.md) |
| Logging & audit | [logging-and-debug.md](./logging-and-debug.md) |

## Security (local-first)

- `add_column` expressions run in the browser via `new Function` — not production-safe.
- Prompts and table samples may be stored in plaintext locally; do not use sensitive data on shared machines.
