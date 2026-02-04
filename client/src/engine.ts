import type { Diff, Plan, SchemaCol } from "./types";

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
