# Technical documentation

Canonical reference for **cursor-for-spreadsheet**, a presentable personal project for applying modern agent architecture to spreadsheet editing — under continuous development, not a frozen prototype.

## Language & audience

| Surface | Language | Audience |
| ------- | -------- | -------- |
| [Root README](../README.md) | English | First-time users, quick start, license |
| [README.cn.md](../README.cn.md) | 中文 | Chinese mirror of root README |
| **`docs/`** (this tree) | English | APIs, contracts, architecture, Agent internals |
| [getting-started.md](./getting-started.md) | English | Detailed setup (Ollama + OpenRouter) |
| [features.md](./features.md) | 中文 | Feature deep dive moved from README |

**Out of scope**: `docs/goals.md` (unused). `.cursor/` is gitignored (local maintainer workspace only).

## Documents

| Document | Audience | Summary |
|----------|----------|---------|
| [product-design-philosophy.md](./product-design-philosophy.md) | Anyone new to the project | Why the project is shaped this way: Plan-as-contract, clarify-before-act, determinism guarding the LLM, bandwidth economics, non-goals |
| [architecture.md](./architecture.md) | Full-stack | Components, API surface, plan execution paths, observability |
| [plan-step-types-reference.md](./plan-step-types-reference.md) | LLM / backend / frontend | Plan JSON contract: every `action`, fields, and semantics |
| [agent-preview-lifecycle.md](./agent-preview-lifecycle.md) | Agent / API | Server-side preview, confirm / abort / revise, fingerprints |
| [agent-stream-sse.md](./agent-stream-sse.md) | Agent / API / frontend | `/api/agent-stream` SSE events, ordering, sync parity, client mapping |
| [agent-memory.md](./agent-memory.md) | Agent / frontend / backend | Workspace memory schema, prompt injection, compaction, optional server session store |
| [client-storage.md](./client-storage.md) | Frontend | Browser `localStorage` keys, workspace memory, session sync, privacy |
| [evaluation.md](./evaluation.md) | Agent quality | Eval suite (`server/evaluation/`): plan/execution/behavior correctness, gates roadmap changes |
| [trouble-shoot.md](./trouble-shoot.md) | Developers | Known issues and common pitfalls |
| [getting-started.md](./getting-started.md) | Setup | Full Quick Start beyond root README |
| [features.md](./features.md) | Users / devs | Features, Agent clarification, preview |
| [logging-and-debug.md](./logging-and-debug.md) | Dev / ops | Trace IDs, audit SQLite, LLM debug NDJSON |

## Source of truth in code

| Concern | Primary files |
|---------|----------------|
| Plan schema (backend) | `server/app/models/plan.py` |
| Plan types (frontend) | `client/src/types.ts` |
| Plan execution (browser) | `client/src/engine.ts` |
| Plan execution (server) | `server/app/services/plan_executor.py` |
| LLM prompts / step rules | `server/app/services/prompt_content.py` |
| Agent orchestration | `server/app/agent/orchestrator.py`, `pa_decision.py`, `pa_tools.py` |
| Agent SSE stream | `server/app/agent/orchestrator.py` (`stream_agent_events`), `client/src/agentStream.ts`, `agentProjectPlan.ts` |
| Agent memory / compaction | `server/app/agent/memory_context.py`, `memory_compaction.py`, `context_assembler.py` |
| Agent preview | `server/app/services/agent_preview.py` |
| Session memory (optional) | `server/app/api/routes/sessions.py`, `services/session_store.py` |
| Workspace memory (client) | `client/src/workspaceMemory.ts`, `sessionMemorySync.ts`, `memoryCompaction.ts` |

## What is not here

- **Maintainer backlog**: local `.cursor/plans/` (gitignored).
- **Personal notes**: optional `docs/local/` (gitignored). Maintainer milestones: `docs/local/PRODUCT_BRIEF.md`. Other typical local-only files: interview notes, resume drafts—never committed.
- **Runbooks**: dev start and test commands in [README](../README.md) (§ Quick start, § Development & testing); pitfalls in [trouble-shoot.md](./trouble-shoot.md).

## When you change the codebase

When you change plan steps, preview APIs, or storage keys:

1. Update the matching doc under `docs/`.
2. If behavior is user-visible, add a short note to the root README.
3. Add or extend tests under `server/tests/` or `client/src/*.test.ts`.
