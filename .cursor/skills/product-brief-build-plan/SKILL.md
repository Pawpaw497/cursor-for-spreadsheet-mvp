---
name: product-brief-build-plan
description: Reads docs/PRODUCT_BRIEF.md as the single source of truth, resolves blocking open questions, and produces a phased build plan with acceptance-linked verification. Use when the user updates the product brief, asks to build or plan from it, or wants milestone execution driven by docs/PRODUCT_BRIEF.md.
---

# Build plan from `docs/PRODUCT_BRIEF.md`

## When to apply

- The user points at `docs/PRODUCT_BRIEF.md` or says they updated the brief, want a build plan, or want to execute a milestone from it.
- Do **not** use this for generic refactors with no link to the brief.

## Prerequisite: load the brief

1. **Read** `docs/PRODUCT_BRIEF.md` in full (all sections 1–8; Appendix A is architectural reference only).
2. If **§1 Status** is `Draft` and **§5** backlog is empty, stop and ask the user to fill at least P0 rows (or define one concrete slice) before planning.

## Gate: blocking work

- **Open questions (§5)**: If any row has no **Resolution** and the question blocks a P0 item, list them first and ask the user to resolve or explicitly defer (move to *Follow-ups* in §7 or mark non-blocking in notes).
- **Non-goals (§4)** and **§3 out-of-scope** cells: treat as hard filters; do not add tasks that violate them.
- **Success criteria (§4)**: every planned deliverable should map to at least one measurable criterion or a demo step in **§7**.

## Produce the build plan (output)

Emit a **Build plan** the user can paste into chat or keep in a PR. Use this structure (concise, ordered):

```markdown
## Build plan: [Title from §1 or §2]

**Source brief**: `docs/PRODUCT_BRIEF.md` (last updated: [from §1])

### Scope
- In scope: [P0/P1 item IDs and one-line names]
- Explicitly out: [from §4 non-goals + §3 out-of-scope]

### Phases
1. **[Phase name]** — goal, deliverables, risks
2. ...

### Work items
| # | Maps to (R-id) | Task | Likely area (client / server / agent / shared) | Verify (Given/When/Then or §7 step) |
|---|----------------|------|-----------------------------------------------|--------------------------------------|

### Verification
- [ ] Demo script from §7: ...
- [ ] Checkboxes from §4: ...

### Doc / repo follow-up
- [ ] **§8 Document changelog**: add row (date, author, summary) after the milestone ships.
- [ ] `README` / `FEATURES.md` / `AGENT_IMPROVEMENTS.md` only if the workspace rules require a user-visible doc update for the change (do not expand docs beyond what the user or rules require).
```

## Execution rules (implementation)

- **Single source of truth**: Do not override the brief with inferred scope; if code and brief disagree, flag it and ask.
- **Align with repo layout**: Use Appendix A and existing patterns (`client/src/`, `server/app/`, `server/app/agent/`, `app/services/tools.py`, `app/services/llm.py`); add tools/prompts in the established paths (see project rules: extend tools + decision loop, no parallel agent stack).
- **Change discipline**: Smallest change that satisfies acceptance; no drive-by refactors; match surrounding style and docstrings (Google style where required by repo).
- **Order work**: P0 by dependency order, then P1; split PR-sized chunks if the user cares about review granularity.

## After implementation

1. Re-read **success criteria (§4)** and **Rollout & verification (§7)**; run or describe the checks.
2. Append **§8 Document changelog** (user may edit author); suggest updating **§1 Last updated** if they own the file.

## Optional: stale Appendix A

If the implementation **changed** which modules own a layer (e.g. new route package), offer a one-paragraph **Appendix A** refresh proposal for the user to paste; do not silently rewrite long baseline tables unless the user asked to sync Appendix A.
