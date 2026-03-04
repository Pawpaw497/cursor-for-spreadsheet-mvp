import type {
  AggregationSpec,
  Diff,
  LookupColumnMapping,
  Plan,
  PlanStep,
  SchemaCol,
  TableData,
} from "./types";

/**
 * Normalizes row expression: if it is a full arrow form (e.g. "row => row.x")
 * returns only the right-hand body so that new Function("row", "return (body)") yields a value.
 * @param expr - Raw expression from plan (may be "row => body" or just "body").
 * @return Expression body to use inside return (body).
 */
function normalizeRowExpression(expr: string): string {
  const match = expr.match(/^\s*(?:\(row\)|row)\s*=>\s*([\s\S]+)$/);
  return match ? match[1].trim() : expr;
}

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
      const body = normalizeRowExpression(step.expression);

      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${body});`) as (
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
      nextRows = nextRows.map((r) => ({
        ...r,
        [col]: transformValue(r[col], kind, args),
      }));
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "sort_table") {
      const col = step.column;
      const order = step.order ?? "ascending";
      nextRows = sortRows(nextRows, col, order);
    }

    if (step.action === "filter_rows") {
      const body = normalizeRowExpression(step.condition);
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${body});`) as (
        row: Record<string, any>
      ) => any;
      nextRows = nextRows.filter((r) => !!safeEval(fn, r));
    }

    if (step.action === "delete_rows") {
      const body = normalizeRowExpression(step.condition);
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${body});`) as (
        row: Record<string, any>
      ) => any;
      nextRows = nextRows.filter((r) => !safeEval(fn, r));
    }

    if (step.action === "deduplicate_rows") {
      const keep = step.keep ?? "first";
      nextRows = deduplicateRows(nextRows, step.keys, keep);
    }

    if (step.action === "rename_column") {
      const from = step.fromName;
      const to = step.toName;
      nextRows = nextRows.map((r) => {
        if (!(from in r)) return r;
        const next = { ...r, [to]: r[from] };
        // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
        delete (next as Record<string, any>)[from];
        return next;
      });
      nextSchema = nextSchema.map((c) =>
        c.key === from ? { ...c, key: to } : c
      );
      if (!diff.modifiedColumns.includes(to)) diff.modifiedColumns.push(to);
    }

    if (step.action === "fill_missing") {
      const col = step.column;
      const fill = computeFillValue(nextRows, col, step.strategy, step.value);
      if (fill !== undefined) {
        nextRows = nextRows.map((r) => {
          const v = r[col];
          return v == null ? { ...r, [col]: fill } : r;
        });
        if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
      }
    }

    if (step.action === "cast_column_type") {
      const col = step.column;
      const target = step.targetType;
      nextRows = nextRows.map((r) => ({ ...r, [col]: castValue(r[col], target) }));
      nextSchema = nextSchema.map((c) =>
        c.key === col ? { ...c, type: target } : c
      );
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "delete_column") {
      const col = step.column;
      nextRows = nextRows.map((r) => {
        if (!(col in r)) return { ...r };
        const next: Record<string, any> = { ...r };
        // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
        delete (next as Record<string, any>)[col];
        return next;
      });
      nextSchema = nextSchema.filter((c) => c.key !== col);
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "reorder_columns") {
      const specified = step.columns;
      const existingKeys = nextSchema.map((c) => c.key);
      const orderedExisting = existingKeys.filter((k) => specified.includes(k));
      const remaining = existingKeys.filter((k) => !specified.includes(k));
      const newOrder = [...orderedExisting, ...remaining];
      nextRows = nextRows.map((r) => {
        const next: Record<string, any> = {};
        for (const k of newOrder) {
          next[k] = r[k];
        }
        return next;
      });
      const byKey = new Map(nextSchema.map((c) => [c.key, c]));
      nextSchema = newOrder
        .map((k) => byKey.get(k))
        .filter((c): c is SchemaCol => !!c);
    }
  }

  return { rows: nextRows, schema: nextSchema, diff };
}

/** Resolve target table name: use step.table or first table if single-table. */
function resolveTable(step: PlanStep, tableNames: string[]): string {
  if ("table" in step && step.table && tableNames.includes(step.table))
    return step.table;
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
      const body = normalizeRowExpression(step.expression);
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${body});`) as (
        r: Record<string, any>
      ) => any;
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
      t.rows = t.rows.map((r) => ({
        ...r,
        [col]: transformValue(r[col], kind, args),
      }));
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "join_tables") {
      const leftT = nextTables[step.left] ?? tables[step.left];
      const rightT = nextTables[step.right] ?? tables[step.right];
      if (!leftT || !rightT) continue;
      const joinType = step.joinType ?? "inner";
      const rows = doJoin(
        leftT.rows,
        rightT.rows,
        step.leftKey,
        step.rightKey,
        joinType
      );
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
        const fn = new Function(
          "rows",
          `return (${step.expression});`
        ) as (r: Record<string, any>[]) => Record<string, any>[];
        rows = fn(rows) ?? rows;
      }
      const schema = inferSchema(rows);
      nextTables[step.name] = { name: step.name, rows, schema };
      newTables.push(step.name);
      tableNames.push(step.name);
    }

    if (step.action === "sort_table") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const order = step.order ?? "ascending";
      t.rows = sortRows(t.rows, step.column, order);
    }

    if (step.action === "filter_rows") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const body = normalizeRowExpression(step.condition);
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${body});`) as (
        row: Record<string, any>
      ) => any;
      t.rows = t.rows.filter((r) => !!safeEval(fn, r));
    }

    if (step.action === "delete_rows") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const body = normalizeRowExpression(step.condition);
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${body});`) as (
        row: Record<string, any>
      ) => any;
      t.rows = t.rows.filter((r) => !safeEval(fn, r));
    }

    if (step.action === "deduplicate_rows") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const keep = step.keep ?? "first";
      t.rows = deduplicateRows(t.rows, step.keys, keep);
    }

    if (step.action === "rename_column") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const from = step.fromName;
      const to = step.toName;
      t.rows = t.rows.map((r) => {
        if (!(from in r)) return r;
        const next = { ...r, [to]: r[from] };
        // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
        delete (next as Record<string, any>)[from];
        return next;
      });
      t.schema = t.schema.map((c) =>
        c.key === from ? { ...c, key: to } : c
      );
      if (!diff.modifiedColumns.includes(to)) diff.modifiedColumns.push(to);
    }

    if (step.action === "fill_missing") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const col = step.column;
      const fill = computeFillValue(t.rows, col, step.strategy, step.value);
      if (fill !== undefined) {
        t.rows = t.rows.map((r) => {
          const v = r[col];
          return v == null ? { ...r, [col]: fill } : r;
        });
        if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
      }
    }

    if (step.action === "cast_column_type") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const col = step.column;
      const target = step.targetType;
      t.rows = t.rows.map((r) => ({ ...r, [col]: castValue(r[col], target) }));
      t.schema = t.schema.map((c) =>
        c.key === col ? { ...c, type: target } : c
      );
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "delete_column") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const col = step.column;
      t.rows = t.rows.map((r) => {
        if (!(col in r)) return { ...r };
        const next: Record<string, any> = { ...r };
        // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
        delete (next as Record<string, any>)[col];
        return next;
      });
      t.schema = t.schema.filter((c) => c.key !== col);
      if (!diff.modifiedColumns.includes(col)) diff.modifiedColumns.push(col);
    }

    if (step.action === "reorder_columns") {
      const tn = resolveTable(step, tableNames);
      const t = nextTables[tn];
      if (!t) continue;
      const specified = step.columns;
      const existingKeys = t.schema.map((c) => c.key);
      const orderedExisting = existingKeys.filter((k) =>
        specified.includes(k)
      );
      const remaining = existingKeys.filter((k) => !specified.includes(k));
      const newOrder = [...orderedExisting, ...remaining];
      t.rows = t.rows.map((r) => {
        const next: Record<string, any> = {};
        for (const k of newOrder) next[k] = r[k];
        return next;
      });
      const byKey = new Map(t.schema.map((c) => [c.key, c]));
      t.schema = newOrder
        .map((k) => byKey.get(k))
        .filter((c): c is SchemaCol => !!c);
    }

    if (step.action === "aggregate_table") {
      const src = nextTables[step.source] ?? tables[step.source];
      if (!src) continue;
      const rows = aggregateTable(src.rows, step.groupBy, step.aggregations);
      const schema = inferSchema(rows);
      nextTables[step.resultTable] = { name: step.resultTable, rows, schema };
      newTables.push(step.resultTable);
      tableNames.push(step.resultTable);
    }

    if (step.action === "union_tables") {
      const mode = step.mode ?? "relaxed";
      const rows = unionTables(nextTables, tables, step.sources, mode);
      const schema = inferSchema(rows);
      nextTables[step.resultTable] = { name: step.resultTable, rows, schema };
      newTables.push(step.resultTable);
      tableNames.push(step.resultTable);
    }

    if (step.action === "lookup_column") {
      const main = nextTables[step.mainTable] ?? tables[step.mainTable];
      const lookup =
        nextTables[step.lookupTable] ?? tables[step.lookupTable];
      if (!main || !lookup) continue;
      const updated = applyLookup(
        main.rows,
        lookup.rows,
        step.mainKey,
        step.lookupKey,
        step.columns
      );
      nextTables[step.mainTable] = {
        ...main,
        rows: updated.rows,
        schema: updated.schema,
      };
      for (const col of updated.addedColumns) {
        if (!diff.addedColumns.includes(col)) diff.addedColumns.push(col);
      }
      for (const col of updated.modifiedColumns) {
        if (!diff.modifiedColumns.includes(col)) {
          diff.modifiedColumns.push(col);
        }
      }
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

function sortRows(
  rows: Record<string, any>[],
  column: string,
  order: "ascending" | "descending"
): Record<string, any>[] {
  const nonNone = rows.filter((r) => r[column] != null);
  const none = rows.filter((r) => r[column] == null);
  nonNone.sort((a, b) => {
    const av = a[column];
    const bv = b[column];
    if (av === bv) return 0;
    return av < bv ? -1 : 1;
  });
  if (order === "descending") nonNone.reverse();
  return [...nonNone, ...none];
}

function deduplicateRows(
  rows: Record<string, any>[],
  keys: string[],
  keep: "first" | "last"
): Record<string, any>[] {
  if (keys.length === 0) return [...rows];
  const map = new Map<string, Record<string, any>>();
  for (const r of rows) {
    const k = JSON.stringify(keys.map((key) => r[key]));
    if (keep === "first") {
      if (!map.has(k)) map.set(k, r);
    } else {
      map.set(k, r);
    }
  }
  return Array.from(map.values());
}

function computeFillValue(
  rows: Record<string, any>[],
  column: string,
  strategy: "constant" | "mean" | "median" | "mode",
  value?: any
): any | undefined {
  const values = rows
    .map((r) => r[column])
    .filter((v) => v != null);
  if (strategy === "constant") return value;
  if (values.length === 0) return undefined;

  if (strategy === "mean" || strategy === "median") {
    const nums = values
      .map((v) => Number(v))
      .filter((n) => !Number.isNaN(n));
    if (nums.length === 0) return undefined;
    if (strategy === "mean") {
      const sum = nums.reduce((acc, n) => acc + n, 0);
      return sum / nums.length;
    }
    const sorted = [...nums].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    if (sorted.length % 2 === 0) {
      return (sorted[mid - 1] + sorted[mid]) / 2;
    }
    return sorted[mid];
  }

  if (strategy === "mode") {
    const freq = new Map<any, number>();
    for (const v of values) {
      freq.set(v, (freq.get(v) ?? 0) + 1);
    }
    let best: any = undefined;
    let bestCount = -1;
    for (const [v, c] of freq.entries()) {
      if (c > bestCount) {
        best = v;
        bestCount = c;
      }
    }
    return best;
  }

  return undefined;
}

function castValue(
  v: any,
  target: "number" | "string" | "date"
): any {
  if (v == null) return v;
  if (target === "string") return String(v);
  if (target === "number") {
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  if (target === "date") {
    const d = new Date(String(v));
    return Number.isNaN(d.getTime()) ? v : d.toISOString().slice(0, 10);
  }
  return v;
}

function aggregateTable(
  rows: Record<string, any>[],
  groupBy: string[],
  aggregations: AggregationSpec[]
): Record<string, any>[] {
  if (groupBy.length === 0) return [];
  const groups = new Map<string, { key: any[]; rows: Record<string, any>[] }>();
  for (const r of rows) {
    const keyVals = groupBy.map((k) => r[k]);
    const k = JSON.stringify(keyVals);
    if (!groups.has(k)) groups.set(k, { key: keyVals, rows: [] });
    groups.get(k)!.rows.push(r);
  }

  const result: Record<string, any>[] = [];
  for (const { key, rows: grp } of groups.values()) {
    const out: Record<string, any> = {};
    groupBy.forEach((name, idx) => {
      out[name] = key[idx];
    });
    for (const agg of aggregations) {
      const vals = grp.map((r) => r[agg.column]).filter((v) => v != null);
      let value: any = null;
      if (agg.op === "count") {
        value = vals.length;
      } else {
        const nums = vals
          .map((v) => Number(v))
          .filter((n) => !Number.isNaN(n));
        if (nums.length === 0) {
          value = null;
        } else if (agg.op === "sum") {
          value = nums.reduce((acc, n) => acc + n, 0);
        } else if (agg.op === "avg") {
          const sum = nums.reduce((acc, n) => acc + n, 0);
          value = sum / nums.length;
        } else if (agg.op === "max") {
          value = Math.max(...nums);
        } else if (agg.op === "min") {
          value = Math.min(...nums);
        }
      }
      out[agg.as] = value;
    }
    result.push(out);
  }
  return result;
}

function unionTables(
  nextTables: Record<string, TableData>,
  originalTables: Record<string, TableData>,
  sources: string[],
  mode: "strict" | "relaxed"
): Record<string, any>[] {
  const tables: TableData[] = [];
  for (const name of sources) {
    const t = nextTables[name] ?? originalTables[name];
    if (t) tables.push(t);
  }
  if (tables.length === 0) return [];

  if (mode === "strict") {
    const commonKeysSet =
      tables
        .map((t) => new Set(t.schema.map((c) => c.key)))
        .reduce<Set<string> | null>((acc, s) => {
          if (!acc) return new Set(s);
          const next = new Set<string>();
          for (const k of acc) {
            if (s.has(k)) next.add(k);
          }
          return next;
        }, null) ?? new Set<string>();
    const keys = Array.from(commonKeysSet.values());
    const rows: Record<string, any>[] = [];
    for (const t of tables) {
      for (const r of t.rows) {
        const next: Record<string, any> = {};
        for (const k of keys) next[k] = r[k];
        rows.push(next);
      }
    }
    return rows;
  }

  const allKeys = new Set<string>();
  for (const t of tables) {
    for (const c of t.schema) allKeys.add(c.key);
  }
  const keys = Array.from(allKeys.values());
  const rows: Record<string, any>[] = [];
  for (const t of tables) {
    for (const r of t.rows) {
      const next: Record<string, any> = {};
      for (const k of keys) next[k] = r[k] ?? null;
      rows.push(next);
    }
  }
  return rows;
}

function applyLookup(
  mainRows: Record<string, any>[],
  lookupRows: Record<string, any>[],
  mainKey: string,
  lookupKey: string,
  columns: LookupColumnMapping[]
): {
  rows: Record<string, any>[];
  schema: SchemaCol[];
  addedColumns: string[];
  modifiedColumns: string[];
} {
  const byKey = new Map<any, Record<string, any>>();
  for (const r of lookupRows) {
    const k = r[lookupKey];
    if (!byKey.has(k)) byKey.set(k, r);
  }

  const addedColumns: string[] = [];
  const modifiedColumns: string[] = [];
  const rows: Record<string, any>[] = [];

  for (const r of mainRows) {
    const k = r[mainKey];
    const match = byKey.get(k);
    const next: Record<string, any> = { ...r };
    for (const col of columns) {
      const to = col.to;
      const value = match ? match[col.from] : null;
      if (to in next && !modifiedColumns.includes(to)) {
        modifiedColumns.push(to);
      }
      if (!(to in next) && !addedColumns.includes(to)) {
        addedColumns.push(to);
      }
      next[to] = value;
    }
    rows.push(next);
  }

  const baseSchema: SchemaCol[] =
    mainRows.length > 0 ? inferSchema(mainRows) : [];
  const schemaKeys = new Set(baseSchema.map((c) => c.key));
  const schema: SchemaCol[] = [...baseSchema];
  for (const col of addedColumns) {
    if (!schemaKeys.has(col)) {
      schema.push({ key: col, type: "string" });
    }
  }

  return { rows, schema, addedColumns, modifiedColumns };
}
