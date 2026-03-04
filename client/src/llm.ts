import { z } from "zod";
import type { Plan, SchemaCol, TableData } from "./types";

const AggregationSpecSchema = z.object({
  column: z.string(),
  op: z.enum(["sum", "avg", "count", "max", "min"]),
  as: z.string()
});

const LookupColumnMappingSchema = z.object({
  from: z.string(),
  to: z.string()
});

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
    action: z.literal("sort_table"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    column: z.string(),
    order: z.enum(["ascending", "descending"])
      .nullish()
      .transform((v) => v ?? "ascending"),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("filter_rows"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    condition: z.string(),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("delete_rows"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    condition: z.string(),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("deduplicate_rows"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    keys: z.array(z.string()).min(1),
    keep: z.enum(["first", "last"])
      .nullish()
      .transform((v) => v ?? "first"),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("rename_column"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    fromName: z.string(),
    toName: z.string(),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("fill_missing"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    column: z.string(),
    strategy: z.enum(["constant", "mean", "median", "mode"]),
    value: z.any().nullish().transform((v) => v ?? undefined),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("cast_column_type"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    column: z.string(),
    targetType: z.enum(["number", "string", "date"]),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("join_tables"),
    left: z.string(),
    right: z.string(),
    leftKey: z.string(),
    rightKey: z.string(),
    resultTable: z.string(),
    joinType: z.enum(["inner", "left", "right"])
      .nullish()
      .transform((v) => v ?? "inner"),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("create_table"),
    name: z.string(),
    source: z.string(),
    expression: z.string().nullish().transform((v) => v ?? undefined),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("aggregate_table"),
    source: z.string(),
    groupBy: z.array(z.string()),
    aggregations: z.array(AggregationSpecSchema),
    resultTable: z.string(),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("union_tables"),
    sources: z.array(z.string()).min(1),
    resultTable: z.string(),
    mode: z.enum(["strict", "relaxed"])
      .nullish()
      .transform((v) => v ?? "relaxed"),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("lookup_column"),
    mainTable: z.string(),
    lookupTable: z.string(),
    mainKey: z.string(),
    lookupKey: z.string(),
    columns: z.array(LookupColumnMappingSchema).min(1),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("delete_column"),
    column: z.string(),
    table: z.string().nullish().transform((v) => v ?? undefined),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("reorder_columns"),
    columns: z.array(z.string()).min(1),
    table: z.string().nullish().transform((v) => v ?? undefined),
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

export type ChatRole = "user" | "assistant" | "system";

export type ChatMessageSource = "live" | "history";

export type ChatMessage = {
  id: string;
  sessionId: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  projectId?: string | null;
  source: ChatMessageSource;
  // meta 保留原始后端 payload 或前端补充信息，用于将来扩展“展开原始 Plan/请求”等高级功能。
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  meta?: any;
};

const API_BASE = "http://localhost:8787";

async function fetchWithTimeout(
  url: string,
  init?: RequestInit,
  timeoutMs = 8000
): Promise<Response> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (e) {
    const err = e as Error;
    // 将超时场景显式转为可读错误，避免请求长时间 pending 导致前端状态悬挂在 Loading。
    if (err.name === "AbortError") {
      throw new Error(
        `请求超时（>${timeoutMs}ms），请检查后端是否在 ${API_BASE} 运行`
      );
    }
    throw e;
  } finally {
    clearTimeout(id);
  }
}

/** 从错误响应的 body 中解析出可读信息；若为 JSON 且含 detail 则返回 detail（后端已含错误号），否则返回 "[status] 原始文本"。 */
function errorMessageFromResponse(resp: Response, txt: string): string {
  try {
    const j = JSON.parse(txt) as { detail?: string };
    if (j && typeof j.detail === "string") return j.detail;
  } catch {
    // ignore
  }
  return txt ? `[${resp.status}] ${txt}` : `[${resp.status}] Request failed`;
}

export async function fetchConfig(): Promise<ConfigResponse> {
  const resp = await fetch(`${API_BASE}/api/config`);
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt));
  }
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
    throw new Error(errorMessageFromResponse(resp, txt));
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
    throw new Error(errorMessageFromResponse(resp, txt));
  }

  const data = await resp.json();
  return PlanSchema.parse(data.plan);
}

type LoadSampleResponse = {
  projectId: string;
  tables: {
    name: string;
    rows: Record<string, any>[];
    schema: SchemaCol[];
  }[];
};

/** 从后端加载 sample.xlsx 中的所有表，转换为 TableData，并返回 projectId。*/
export async function fetchSampleTables(opts?: {
  /** 单次请求的超时时间（毫秒），未指定时默认 8000ms。 */
  timeoutMs?: number;
}): Promise<{ projectId: string; tables: TableData[] }> {
  const timeoutMs = opts?.timeoutMs ?? 8000;
  const resp = await fetchWithTimeout(`${API_BASE}/api/load-sample`, undefined, timeoutMs);
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt || "Failed to load sample tables"));
  }
  const data = (await resp.json()) as LoadSampleResponse;
  const tables = data.tables.map((t) => ({
    name: t.name,
    rows: t.rows,
    schema: t.schema
  }));
  return { projectId: data.projectId, tables };
}

/** 上传 Excel/CSV 文件到后端，创建新的 ProjectState 并返回 projectId 与表列表。*/
export async function uploadProjectFile(
  file: File
): Promise<{ projectId: string; tables: TableData[] }> {
  const form = new FormData();
  form.append("file", file);

  // 导入场景给更长一点的超时时间，以兼容体积较大的业务文件。
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/import-file`,
    {
      method: "POST",
      body: form
    },
    20000
  );

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt || "导入失败"));
  }

  const data = (await resp.json()) as LoadSampleResponse;
  const tables = data.tables.map((t) => ({
    name: t.name,
    rows: t.rows,
    schema: t.schema
  }));
  return { projectId: data.projectId, tables };
}

/** 带重试的加载示例表格，用于启动时后端可能尚未就绪的场景。 */
export async function fetchSampleTablesWithRetry(opts?: {
  maxRetries?: number;
  delayMs?: number;
  timeoutMs?: number;
}): Promise<{ projectId: string; tables: TableData[] }> {
  const maxRetries = opts?.maxRetries ?? 4;
  const delayMs = opts?.delayMs ?? 1500;
  const timeoutMs = opts?.timeoutMs ?? 8000;
  let lastError: Error | null = null;
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fetchSampleTables({ timeoutMs });
    } catch (e) {
      lastError = e instanceof Error ? e : new Error(String(e));
      if (i < maxRetries - 1) {
        await new Promise((r) => setTimeout(r, delayMs));
      }
    }
  }
  throw lastError ?? new Error("加载示例失败");
}

/** 基于后端 ProjectState 生成项目级 Plan。*/
export async function requestProjectPlanById(opts: {
  projectId: string;
  prompt: string;
  modelSource?: ModelSource;
  cloudModelId?: string;
  localModelId?: string;
}): Promise<Plan> {
  const payload = {
    prompt: opts.prompt,
    modelSource: opts.modelSource ?? "cloud",
    cloudModelId: opts.cloudModelId ?? undefined,
    localModelId: opts.localModelId ?? undefined
  };
  const resp = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(opts.projectId)}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt));
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
    throw new Error(errorMessageFromResponse(resp, txt || "导出失败"));
  }
  return resp.blob();
}

type ExecutePlanResponse = {
  tables: Record<
    string,
    {
      name: string;
      rows: Record<string, any>[];
      schema: SchemaCol[];
    }
  >;
  diff: {
    addedColumns: string[];
    modifiedColumns: string[];
  };
  newTables: string[];
};

/** 调用后端 /api/execute-plan，在服务端执行 Plan 并返回更新后的表与 Diff。 */
export async function executePlanOnServer(opts: {
  tables: Record<string, TableData>;
  plan: Plan;
}): Promise<{ tables: Record<string, TableData>; diff: ExecutePlanResponse["diff"]; newTables: string[] }> {
  const payload = {
    plan: opts.plan,
    tables: Object.values(opts.tables).map((t) => ({
      name: t.name,
      rows: t.rows,
      schema: t.schema
    }))
  };

  const resp = await fetch(`${API_BASE}/api/execute-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt || "执行 Plan 失败"));
  }

  const data = (await resp.json()) as ExecutePlanResponse;
  const nextTables: Record<string, TableData> = {};
  for (const [name, t] of Object.entries(data.tables)) {
    nextTables[name] = {
      name: t.name,
      rows: t.rows,
      schema: t.schema
    };
  }
  return { tables: nextTables, diff: data.diff, newTables: data.newTables };
}

/** 基于后端 ProjectState 执行 Plan，并返回最新表状态。*/
export async function executeProjectPlanById(opts: {
  projectId: string;
  plan: Plan;
}): Promise<{ tables: Record<string, TableData>; diff: ExecutePlanResponse["diff"]; newTables: string[] }> {
  const resp = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(opts.projectId)}/execute-plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: opts.plan })
    }
  );

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt || "执行 Project Plan 失败"));
  }

  const data = (await resp.json()) as ExecutePlanResponse;
  const nextTables: Record<string, TableData> = {};
  for (const [name, t] of Object.entries(data.tables)) {
    nextTables[name] = {
      name: t.name,
      rows: t.rows,
      schema: t.schema
    };
  }
  return { tables: nextTables, diff: data.diff, newTables: data.newTables };
}

type ChatHistoryApiMessage = Omit<ChatMessage, "source" | "createdAt"> & {
  createdAt: string | Date;
};

type ChatHistoryResponse = {
  messages: ChatHistoryApiMessage[];
};

/** 调用后端 /api/chat-history，拉取聚合后的历史对话消息。 */
export async function fetchChatHistory(opts?: {
  projectId?: string;
  limit?: number;
}): Promise<ChatMessage[]> {
  const params = new URLSearchParams();
  if (opts?.projectId) {
    params.set("projectId", opts.projectId);
  }
  if (typeof opts?.limit === "number") {
    params.set("limit", String(opts.limit));
  }
  const query = params.toString();
  const url = query
    ? `${API_BASE}/api/chat-history?${query}`
    : `${API_BASE}/api/chat-history`;

  const resp = await fetchWithTimeout(url);
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt || "加载历史对话失败"));
  }

  const data = (await resp.json()) as ChatHistoryResponse;
  const normalized: ChatMessage[] = (data.messages ?? []).map((m) => {
    const createdAt =
      typeof m.createdAt === "string"
        ? m.createdAt
        : m.createdAt.toISOString();
    return {
      ...m,
      createdAt,
      source: "history"
    };
  });

  // 前端统一按时间正序展示，便于阅读。
  normalized.sort(
    (a, b) =>
      new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
  );
  return normalized;
}
