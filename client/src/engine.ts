import type { Diff, Plan, PlanStep, SchemaCol, TableData } from "./types";

export function inferSchema(rows: Record<string, any>[]): SchemaCol[] {
  const keys = Object.keys(rows[0] ?? {});
  return keys.map((k) => {
    const v = rows.find((r) => r[k] != null)?.[k];
    const t =
      typeof v === "number" ? "number" : v instanceof Date ? "date" : "string";
    return { key: k, type: t };
  });
}

export function applyPlan(
  rows: Record<string, any>[],
  schema: SchemaCol[],
  plan: Plan
): { rows: Record<string, any>[]; schema: SchemaCol[]; diff: Diff } {
  let nextRows = [...rows];
  let nextSchema = [...schema];
  const diff: Diff = { addedColumns: [], modifiedColumns: [] };

  for (const step of plan.steps) {
    if (step.action === "add_column") {
      const name = step.name;
      const expr = step.expression;

      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${expr});`) as (
        row: Record<string, any>
      ) => any;

      nextRows = nextRows.map((r) => ({ ...r, [name]: safeEval(fn, r) }));
      if (!nextSchema.find((c) => c.key === name)) {
        nextSchema.push({ key: name, type: "string" });
        diff.addedColumns.push(name);
      } else {
        diff.modifiedColumns.push(name);
      }
    }

    if (step.action === "transform_column") {
      const col = step.column;
      const kind = step.transform;
      const args = step.args ?? {};
      nextRows = nextRows.map((r) => ({ ...r, [col]: transformValue(r[col], kind, args) }));
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }
  }

  return { rows: nextRows, schema: nextSchema, diff };
}

/** Resolve target table name: use step.table or first table if single-table. */
function resolveTable(step: PlanStep, tableNames: string[]): string {
  if ("table" in step && step.table && tableNames.includes(step.table)) return step.table;
  return tableNames[0] ?? "";
}

export type ProjectApplyResult = {
  tables: Record<string, TableData>;
  diff: Diff;
  newTables: string[];
};

export function applyProjectPlan(
  tables: Record<string, TableData>,
  plan: Plan
): ProjectApplyResult {
  const nextTables: Record<string, TableData> = {};
  for (const [k, v] of Object.entries(tables)) {
    nextTables[k] = { name: k, rows: [...v.rows], schema: [...v.schema] };
  }
  const diff: Diff = { addedColumns: [], modifiedColumns: [] };
  const newTables: string[] = [];
  const tableNames = Object.keys(tables);

  for (const step of plan.steps) {
    if (step.action === "add_column") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const name = step.name;
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${step.expression});`) as (r: Record<string, any>) => any;
      t.rows = t.rows.map((r) => ({ ...r, [name]: safeEval(fn, r) }));
      if (!t.schema.find((c) => c.key === name)) {
        t.schema.push({ key: name, type: "string" });
        diff.addedColumns.push(name);
      } else diff.modifiedColumns.push(name);
    }

    if (step.action === "transform_column") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const col = step.column;
      const kind = step.transform;
      const args = step.args ?? {};
      t.rows = t.rows.map((r) => ({ ...r, [col]: transformValue(r[col], kind, args) }));
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "join_tables") {
      const leftT = nextTables[step.left] ?? tables[step.left];
      const rightT = nextTables[step.right] ?? tables[step.right];
      if (!leftT || !rightT) continue;
      const joinType = step.joinType ?? "inner";
      const rows = doJoin(leftT.rows, rightT.rows, step.leftKey, step.rightKey, joinType);
      const schema = inferSchema(rows);
      nextTables[step.resultTable] = { name: step.resultTable, rows, schema };
      newTables.push(step.resultTable);
      tableNames.push(step.resultTable);
    }

    if (step.action === "create_table") {
      const src = nextTables[step.source] ?? tables[step.source];
      if (!src) continue;
      let rows = [...src.rows];
      if (step.expression) {
        // eslint-disable-next-line no-new-func
        const fn = new Function("rows", `return (${step.expression});`) as (r: Record<string, any>[]) => Record<string, any>[];
        rows = fn(rows) ?? rows;
      }
      const schema = inferSchema(rows);
      nextTables[step.name] = { name: step.name, rows, schema };
      newTables.push(step.name);
      tableNames.push(step.name);
    }
  }

  return { tables: nextTables, diff, newTables };
}

function doJoin(
  left: Record<string, any>[],
  right: Record<string, any>[],
  leftKey: string,
  rightKey: string,
  joinType: "inner" | "left" | "right"
): Record<string, any>[] {
  const rightByKey = new Map<any, Record<string, any>[]>();
  for (const r of right) {
    const k = r[rightKey];
    if (!rightByKey.has(k)) rightByKey.set(k, []);
    rightByKey.get(k)!.push(r);
  }
  const leftByKey = new Map<any, Record<string, any>[]>();
  for (const r of left) {
    const k = r[leftKey];
    if (!leftByKey.has(k)) leftByKey.set(k, []);
    leftByKey.get(k)!.push(r);
  }
  const result: Record<string, any>[] = [];

  if (joinType === "right") {
    for (const r of right) {
      const k = r[rightKey];
      const matches = leftByKey.get(k) ?? [];
      if (matches.length === 0) {
        result.push({ ...emptyWithLeftSchema(left), ...prefixKeys(r, "right_") });
      } else {
        for (const m of matches) {
          result.push({ ...m, ...prefixKeys(r, "right_") });
        }
      }
    }
    return result;
  }

  for (const l of left) {
    const k = l[leftKey];
    const matches = rightByKey.get(k) ?? [];
    if (matches.length === 0 && joinType === "inner") continue;
    if (matches.length === 0 && joinType === "left") {
      result.push({ ...l, ...emptyWithRightSchema(right) });
    } else {
      for (const m of matches) {
        result.push({ ...l, ...prefixKeys(m, "right_") });
      }
    }
  }
  return result;
}

function prefixKeys(obj: Record<string, any>, prefix: string): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(obj)) out[prefix + k] = v;
  return out;
}

function emptyWithRightSchema(right: Record<string, any>[]): Record<string, any> {
  const sample = right[0];
  if (!sample) return {};
  const out: Record<string, any> = {};
  for (const k of Object.keys(sample)) out["right_" + k] = null;
  return out;
}

function emptyWithLeftSchema(left: Record<string, any>[]): Record<string, any> {
  const sample = left[0];
  if (!sample) return {};
  const out: Record<string, any> = {};
  for (const k of Object.keys(sample)) out[k] = null;
  return out;
}

function safeEval(fn: (row: Record<string, any>) => any, row: Record<string, any>) {
  try {
    return fn(row);
  } catch {
    return null;
  }
}

function transformValue(v: any, kind: string, args: Record<string, any>) {
  if (v == null) return v;
  const s = String(v);

  switch (kind) {
    case "trim":
      return s.trim();
    case "lower":
      return s.toLowerCase();
    case "upper":
      return s.toUpperCase();
    case "replace": {
      const from = String(args.from ?? "");
      const to = String(args.to ?? "");
      return s.split(from).join(to);
    }
    case "parse_date": {
      const d = new Date(s);
      return isNaN(d.getTime()) ? v : d.toISOString().slice(0, 10);
    }
    default:
      return v;
  }
}
