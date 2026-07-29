# Getting started (detailed)

Step-by-step setup beyond the [root README Quick Start](../README.md). Workflow overview: [demo-flow.svg](./assets/demo-flow.svg). For architecture and APIs see [architecture.md](./architecture.md).

Optional one-liner from repo root: `make dev` (background API + Vite; stop processes manually).

## Prerequisites

| Tool | Check | Notes |
|------|-------|-------|
| Python 3.10+ | `python3 --version` | Backend uses `uv` in `server/` |
| [uv](https://docs.astral.sh/uv/) | `uv --version` | Creates `server/.venv` via `uv sync` |
| Node.js 18+ | `node -v` | Frontend in `client/` |
| Ollama (local path) | `ollama --version` | Optional if using OpenRouter only |

**Environment convention:** backend runtime is **`server/.venv` only** (from `uv sync`). Do not use repo-root `env/`, `.venv`, or `venv` for this project.

If your default `python` is Conda and you want an isolated backend env:

```bash
cd server
uv python pin 3.11   # or set UV_PYTHON
uv sync
```

## Path A — Ollama only (no API key)

See README § Quick Start. Summary:

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

If Ollama calls fail with 503, disable VPN or add `localhost:11434` to the proxy bypass list.

## Path B — OpenRouter (cloud)

1. Create an API key at [openrouter.ai](https://openrouter.ai).
2. In `server/.env`:

```env
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=openrouter/auto
```

3. Optional: override `OPENROUTER_MODELS` and `OPENROUTER_LABELS` (comma-separated, equal length) for the UI dropdown. `OPENROUTER_MODELS` entries must be OpenRouter model ids (`vendor/model`, e.g. `anthropic/claude-3.5-sonnet`); labels are the display names shown on OpenRouter. Defaults are in `.env.example`.
4. Start backend and frontend as in Path A.

**Cost tip:** for repeated Plan/Agent debugging pick a cheap model (Gemini Flash Lite, GPT-4o-mini); switch to a stronger one when comparing output quality.

**Optional cloud E2E** (real API calls, needs key):

```bash
cd server
RUN_CLOUD_LLM_E2E=1 E2E_CLOUD_MODEL_ID=google/gemini-2.5-flash-lite \
  uv run pytest tests/test_cloud_llm_sample_e2e.py -q
```

## Backend `.env` reference

Copy from `server/.env.example`. Common keys:

| Key | Purpose |
|-----|---------|
| `OPENROUTER_API_KEY` | Cloud LLM; leave empty for Ollama-only |
| `OPENROUTER_MODEL` / `OPENROUTER_MODELS` / `OPENROUTER_LABELS` | Cloud model list for UI |
| `OLLAMA_BASE` / `OLLAMA_MODEL` / `OLLAMA_MODELS` / `OLLAMA_LABELS` | Local models |
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
4. Pick **本地** and `qwen2.5:7b` (or a cloud model).
5. Try prompts aligned with sample column names — see [test-data/test-prompts.md](../test-data/test-prompts.md).
6. **Generate Plan** → diff highlights + Diff Preview bar.
7. **Apply** → backend executes plan; **撤销** restores pre-apply snapshot.

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
