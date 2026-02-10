import { z } from "zod";
import type { Plan, PlanStep, SchemaCol, TableData } from "./types";

const StepSchema = z.union([
  z.object({
    action: z.literal("add_column"),
    name: z.string().min(1),
    expression: z.string().min(1),
    table: z.string().nullish().transform((v) => v ?? undefined),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("transform_column"),
    column: z.string().min(1),
    transform: z.enum(["trim", "lower", "upper", "replace", "parse_date"]),
    args: z.record(z.any()).nullish().transform((v) => v ?? undefined),
    table: z.string().nullish().transform((v) => v ?? undefined),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("join_tables"),
    left: z.string(),
    right: z.string(),
    leftKey: z.string(),
    rightKey: z.string(),
    resultTable: z.string(),
    joinType: z.enum(["inner", "left", "right"]).nullish().transform((v) => v ?? "inner"),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("create_table"),
    name: z.string(),
    source: z.string(),
    expression: z.string().nullish().transform((v) => v ?? undefined),
    note: z.string().nullish().transform((v) => v ?? undefined)
  })
]);

const PlanSchema = z.object({
  intent: z.string().min(1),
  steps: z.array(StepSchema).min(1)
});

export type ModelSource = "cloud" | "local";

export type ModelOption = { id: string; label: string };

export type ConfigResponse = {
  openRouterModel: string;
  openRouterModels: ModelOption[];
  ollamaModel: string;
  ollamaModels: ModelOption[];
};

const API_BASE = "http://localhost:8787";

export async function fetchConfig(): Promise<ConfigResponse> {
  const resp = await fetch(`${API_BASE}/api/config`);
  if (!resp.ok) throw new Error("Failed to fetch config");
  return resp.json();
}

export async function requestPlan(opts: {
  prompt: string;
  schema: SchemaCol[];
  sampleRows: Record<string, any>[];
  modelSource?: ModelSource;
  cloudModelId?: string;
  localModelId?: string;
}): Promise<Plan> {
  const resp = await fetch(`${API_BASE}/api/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...opts,
      modelSource: opts.modelSource ?? "cloud",
      cloudModelId: opts.cloudModelId ?? undefined,
      localModelId: opts.localModelId ?? undefined
    })
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(txt);
  }

  const data = await resp.json();
  return PlanSchema.parse(data.plan);
}

export async function requestProjectPlan(opts: {
  prompt: string;
  tables: TableData[];
  modelSource?: ModelSource;
  cloudModelId?: string;
  localModelId?: string;
}): Promise<Plan> {
  const payload = {
    prompt: opts.prompt,
    tables: opts.tables.map((t) => ({
      name: t.name,
      schema: t.schema,
      sampleRows: t.rows.slice(0, 10)
    })),
    modelSource: opts.modelSource ?? "cloud",
    cloudModelId: opts.cloudModelId ?? undefined,
    localModelId: opts.localModelId ?? undefined
  };
  const resp = await fetch(`${API_BASE}/api/plan-project`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(txt);
  }

  const data = await resp.json();
  return PlanSchema.parse(data.plan);
}

/** 将当前项目导出为 Excel 文件，返回 Blob。*/
export async function exportProjectToExcel(tables: TableData[]): Promise<Blob> {
  const resp = await fetch(`${API_BASE}/api/export-excel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tables })
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(txt || "导出失败");
  }
  return resp.blob();
}
