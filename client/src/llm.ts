import { z } from "zod";
import type { Plan, SchemaCol } from "./types";

const PlanSchema = z.object({
  intent: z.string().min(1),
  steps: z.array(
    z.union([
      z.object({
        action: z.literal("add_column"),
        name: z.string().min(1),
        expression: z.string().min(1),
        note: z.string().nullish().transform((v) => v ?? undefined)
      }),
      z.object({
        action: z.literal("transform_column"),
        column: z.string().min(1),
        transform: z.enum(["trim", "lower", "upper", "replace", "parse_date"]),
        args: z.record(z.any()).nullish().transform((v) => v ?? undefined),
        note: z.string().nullish().transform((v) => v ?? undefined)
      })
    ])
  ).min(1)
});

export type ModelSource = "cloud" | "local";

export async function requestPlan(opts: {
  prompt: string;
  schema: SchemaCol[];
  sampleRows: Record<string, any>[];
  modelSource?: ModelSource;
}): Promise<Plan> {
  const resp = await fetch("http://localhost:8787/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...opts, modelSource: opts.modelSource ?? "cloud" })
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(txt);
  }

  const data = await resp.json();
  return PlanSchema.parse(data.plan);
}
