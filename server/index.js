import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import { z } from "zod";

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json({ limit: "1mb" }));

const PlanSchema = z.object({
  intent: z.string().min(1),
  steps: z.array(
    z.discriminatedUnion("action", [
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

function buildPrompt({ userPrompt, schema, sampleRows }) {
  return `
You are an agent that edits a spreadsheet grid.
Return ONLY valid JSON that matches this schema:

{
  "intent": string,
  "steps": [
    { "action": "add_column", "name": string, "expression": string, "note"?: string }
    OR
    { "action": "transform_column", "column": string, "transform": "trim"|"lower"|"upper"|"replace"|"parse_date", "args"?: object, "note"?: string }
  ]
}

Rules:
- Use only columns that exist in the schema for reading.
- For add_column.expression:
  - It's a JavaScript expression evaluated with (row) => <expression>
  - Use row.<colName> to reference values.
  - Keep it simple: arithmetic, string concat, ternary, Number(), String(), Math.*
- For transform_column:
  - replace args: { "from": string, "to": string }
  - parse_date args: { "formatHint"?: string } (hint only)
- If the user asks something out of scope, still return the closest minimal plan.

Spreadsheet schema:
${JSON.stringify(schema, null, 2)}

Sample rows:
${JSON.stringify(sampleRows, null, 2)}

User request:
${userPrompt}
`.trim();
}

app.post("/api/plan", async (req, res) => {
  try {
    const { prompt, schema, sampleRows } = req.body ?? {};
    if (!prompt || !schema || !sampleRows) {
      return res.status(400).json({ error: "Missing prompt/schema/sampleRows" });
    }

    const apiKey = process.env.OPENROUTER_API_KEY;
    const model = process.env.MODEL || "openai/gpt-4.1-mini";
    if (!apiKey) return res.status(500).json({ error: "OPENROUTER_API_KEY missing" });

    const llmPrompt = buildPrompt({ userPrompt: prompt, schema, sampleRows });

    const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model,
        temperature: 0.1,
        messages: [
          { role: "system", content: "You output strict JSON only." },
          { role: "user", content: llmPrompt }
        ]
      })
    });

    if (!resp.ok) {
      const txt = await resp.text();
      return res.status(502).json({ error: "OpenRouter error", detail: txt });
    }

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content ?? "";
    let parsed;
    try {
      parsed = JSON.parse(content);
    } catch {
      // Sometimes models wrap in ```json ... ```
      const cleaned = content.replace(/```json|```/g, "").trim();
      parsed = JSON.parse(cleaned);
    }

    const plan = PlanSchema.parse(parsed);
    return res.json({ plan });
  } catch (e) {
    return res.status(500).json({ error: "Server error", detail: String(e) });
  }
});

app.get("/health", (_, res) => res.json({ ok: true }));

const port = 8787;
app.listen(port, () => console.log(`server listening on http://localhost:${port}`));
