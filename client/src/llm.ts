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
        note: z.string().optional()
      }),
      z.object({
        action: z.literal("transform_column"),
        column: z.string().min(1),
        transform: z.enum(["trim", "lower", "upper", "replace", "parse_date"]),
        args: z.record(z.any()).optional(),
        note: z.string().optional()
      })
    ])
  ).min(1)
});

export async function requestPlan(opts: {
  prompt: string;
  schema: SchemaCol[];
  sampleRows: Record<string, any>[];
}): Promise<Plan> {
  const resp = await fetch("http://localhost:8787/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts)
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(txt);
  }

  const data = await resp.json();
  return PlanSchema.parse(data.plan);
}
