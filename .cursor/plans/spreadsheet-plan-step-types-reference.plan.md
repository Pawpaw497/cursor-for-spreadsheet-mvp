---
name: Plan step types and executor reference
overview: Single reference for supported Plan step types (schema + executor + tools), plus a short user-facing summary and extension ideas.
todos: []
isProject: false
---

# Plan step types & executor reference (merged)

**Supersedes:** `spreadsheet-plan-operation-types_95350f32.plan.md`, `spreadsheet-supported-ops_15df3a60.plan.md` (duplicate content with different emphasis).

## Objectives

- List **Plan / Step** types and fields as used by the backend.
- Describe **where** each applies: single-table vs project (multi-table) execution.
- Point to **plan_executor** and **tools** behavior.
- Note **future** extensions (numeric transforms, new step kinds).

## Quick answer (supported operation classes)

At Plan level, the backend supports these **step actions**:

- **Column derive / compute**: `add_column` — expression per row (sandboxed eval).
- **Column clean / format**: `transform_column` — `TransformKind`: `trim`, `lower`, `upper`, `replace`, `parse_date`.
- **Join**: `join_tables` — inner / left / right, new `resultTable`.
- **New table**: `create_table` — from `source` with optional table-level expression.

## Plan schema (`server/app/models/plan.py`)

- **Plan**: `intent: str`, `steps: List[Step]` (at least one step).
- **Step union** (baseline reference doc; codebase may add more over time):  
  `AddColumnStep` | `TransformColumnStep` | `JoinTablesStep` | `CreateTableStep`

### `add_column`

- Fields: `action="add_column"`, `name`, `expression`, optional `table`, `note`.
- Semantics: compute a new column (or overwrite if name exists) from row expression; conventionally JS arrow `row => ...` in prompts — normalize to expression body in executor if needed.

### `transform_column`

- Fields: `action="transform_column"`, `column`, `transform`, optional `args`, `table`, `note`.
- `TransformKind`: string enum as above.

### `join_tables`

- Fields: `action="join_tables"`, `left`, `right`, `leftKey`, `rightKey`, `resultTable`, optional `joinType` (`inner` | `left` | `right`), `note`.

### `create_table`

- Fields: `action="create_table"`, `name`, `source`, optional `expression`, `note`.
- Semantics: copy/transform from `source` into new table `name` (project executor).

## Executor (`server/app/services/plan_executor.py`)

- **`apply_plan` (single table)**: typically `add_column`, `transform_column` (and any single-table steps your code supports).
- **`apply_project_plan` (multi-table)**: all four actions above, with `_resolve_table_name` / table routing as implemented.
- **Diff**: `addedColumns`, `modifiedColumns`, `newTables` per current engine behavior.

## Agent tools (`server/app/services/tools.py`)

- Helpers such as `get_schema`, `get_sample_rows`, `get_column_stats`, `validate_expression` — they **do not** add new Step types; they help the LLM emit valid steps.

## Extension ideas (not implemented)

- Extend **`TransformKind`** for numeric ops (`add_number`, `multiply`, …) and implement in `_transform_value` (or equivalent).
- Or add a dedicated step (e.g. numeric op / pivot) — update `Step` union, executor, frontend `engine.ts`, and prompts together.

## Documentation hygiene

- Keep JSON examples for each step in README or FEATURES to reduce invalid LLM output.
- Align frontend `types.ts` / `engine.ts` with `plan.py` whenever a new `action` is added.
