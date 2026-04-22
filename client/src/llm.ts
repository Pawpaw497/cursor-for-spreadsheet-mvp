import { z } from "zod";
import {
  generateTraceId,
  logError,
  logInfo
} from "./logger";
import type { Diff, Plan, SchemaCol, TableData } from "./types";

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
  }),
  z.object({
    action: z.literal("validate_table"),
    table: z.string().nullish().transform((v) => v ?? undefined),
    rules: z.array(z.string()).min(1),
    level: z
      .enum(["warn", "error"])
      .nullish()
      .transform((v) => v ?? "warn"),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("pivot_table"),
    source: z.string(),
    index: z.array(z.string()).min(1),
    columns: z.string(),
    values: z.string(),
    agg: z
      .enum(["sum", "count", "avg", "max", "min"])
      .nullish()
      .transform((v) => v ?? "sum"),
    resultTable: z.string(),
    note: z.string().nullish().transform((v) => v ?? undefined)
  }),
  z.object({
    action: z.literal("unpivot_table"),
    source: z.string(),
    idVars: z.array(z.string()).min(1),
    valueVars: z.array(z.string()).min(1),
    varName: z
      .string()
      .nullish()
      .transform((v) => v ?? "variable"),
    valueName: z
      .string()
      .nullish()
      .transform((v) => v ?? "value"),
    resultTable: z.string(),
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

const TIMEOUT_DEFAULT_MS = 8000;
const TIMEOUT_LLM_MS = 180000;
const TIMEOUT_IMPORT_MS = 20000;
const TIMEOUT_EXECUTE_MS = 120000;
const TIMEOUT_EXPORT_MS = 60000;

type FetchLogOptions = {
  /** 用于控制台与后端对齐的请求关联 ID。 */
  traceId?: string;
  /** 业务操作名，如 request_plan。 */
  operation: string;
};

function requestPath(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url;
  }
}

/**
 * 带超时、X-Request-ID 与 request_* 日志的 fetch。
 *
 * @param url 请求 URL。
 * @param init fetch init。
 * @param timeoutMs 超时毫秒。
 * @param logOptions 日志与 trace；未传 traceId 时自动生成。
 */
async function fetchWithTimeout(
  url: string,
  init?: RequestInit,
  timeoutMs = TIMEOUT_DEFAULT_MS,
  logOptions?: FetchLogOptions
): Promise<Response> {
  const traceId = logOptions?.traceId ?? generateTraceId();
  const operation = logOptions?.operation ?? "http";
  const path = requestPath(url);
  const method = (init?.method ?? "GET").toUpperCase();

  const headers = new Headers(init?.headers);
  if (!headers.has("X-Request-ID")) {
    headers.set("X-Request-ID", traceId);
  }

  logInfo("request_start", { operation, path, traceId, method });

  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  const t0 = performance.now();
  try {
    const resp = await fetch(url, {
      ...init,
      headers,
      signal: controller.signal
    });
    const durationMs = Math.round(performance.now() - t0);
    if (!resp.ok) {
      logError("request_error", {
        operation,
        path,
        traceId,
        status: resp.status,
        durationMs,
        message: `HTTP ${resp.status}`
      });
    } else {
      logInfo("request_success", {
        operation,
        path,
        traceId,
        status: resp.status,
        durationMs
      });
    }
    return resp;
  } catch (e) {
    const err = e as Error;
    const durationMs = Math.round(performance.now() - t0);
    const isTimeout = err.name === "AbortError";
    logError("request_error", {
      operation,
      path,
      traceId,
      isTimeout,
      durationMs,
      message: isTimeout
        ? `请求超时（>${timeoutMs}ms），请检查后端是否在 ${API_BASE} 运行`
        : err.message
    });
    if (isTimeout) {
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

/**
 * 拆分 FastAPI `detail` 中的主句与「技术详情」尾段（若存在）。
 *
 * Plan 路由在 OpenRouter 鉴权失败时会在 detail 末尾附加 `（技术详情：...）`，
 * 便于 UI 短提示与日志长尾分离。
 *
 * @param detail HTTP JSON body 中的 `detail` 字段或等价字符串。
 * @returns short 为截断技术尾后的文案；technical 为尾段内容（不含括号标签）。
 */
export function splitApiErrorDetail(detail: string): {
  short: string;
  technical?: string;
} {
  const marker = "（技术详情：";
  const i = detail.indexOf(marker);
  if (i >= 0) {
    const short = detail.slice(0, i).trim();
    let tech = detail.slice(i + marker.length);
    if (tech.endsWith("）")) {
      tech = tech.slice(0, -1);
    }
    return { short, technical: tech };
  }
  return { short: detail };
}

export async function fetchConfig(): Promise<ConfigResponse> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/config`,
    undefined,
    TIMEOUT_DEFAULT_MS,
    { operation: "fetch_config" }
  );
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
  /** 与 UI 事件对齐的请求 ID；缺省时由 fetch 层生成。 */
  traceId?: string;
}): Promise<Plan> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: opts.prompt,
        schema: opts.schema,
        sampleRows: opts.sampleRows,
        modelSource: opts.modelSource ?? "cloud",
        cloudModelId: opts.cloudModelId ?? undefined,
        localModelId: opts.localModelId ?? undefined
      })
    },
    TIMEOUT_LLM_MS,
    { operation: "request_plan", traceId: opts.traceId }
  );

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
  traceId?: string;
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
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/plan-project`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    },
    TIMEOUT_LLM_MS,
    { operation: "request_project_plan", traceId: opts.traceId }
  );

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
  traceId?: string;
}): Promise<{ projectId: string; tables: TableData[] }> {
  const timeoutMs = opts?.timeoutMs ?? TIMEOUT_DEFAULT_MS;
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/load-sample`,
    undefined,
    timeoutMs,
    { operation: "load_sample", traceId: opts?.traceId }
  );
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
  const traceId = generateTraceId();
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/import-file`,
    {
      method: "POST",
      body: form
    },
    TIMEOUT_IMPORT_MS,
    { operation: "import_file", traceId }
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
  traceId?: string;
}): Promise<Plan> {
  const payload = {
    prompt: opts.prompt,
    modelSource: opts.modelSource ?? "cloud",
    cloudModelId: opts.cloudModelId ?? undefined,
    localModelId: opts.localModelId ?? undefined
  };
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/projects/${encodeURIComponent(opts.projectId)}/plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    },
    TIMEOUT_LLM_MS,
    { operation: "request_project_plan_by_id", traceId: opts.traceId }
  );

  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt));
  }

  const data = await resp.json();
  return PlanSchema.parse(data.plan);
}

/** 将当前项目导出为 Excel 文件，返回 Blob。*/
export async function exportProjectToExcel(
  tables: TableData[],
  opts?: { traceId?: string }
): Promise<Blob> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/export-excel`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tables })
    },
    TIMEOUT_EXPORT_MS,
    { operation: "export_excel", traceId: opts?.traceId }
  );
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
    validationWarnings: string[];
    validationErrors: string[];
  };
  newTables: string[];
};

function normalizeExecuteDiff(
  d: Partial<ExecutePlanResponse["diff"]> | undefined
): Diff {
  return {
    addedColumns: d?.addedColumns ?? [],
    modifiedColumns: d?.modifiedColumns ?? [],
    validationWarnings: d?.validationWarnings ?? [],
    validationErrors: d?.validationErrors ?? []
  };
}

/** 调用后端 /api/execute-plan，在服务端执行 Plan 并返回更新后的表与 Diff。 */
export async function executePlanOnServer(opts: {
  tables: Record<string, TableData>;
  plan: Plan;
  traceId?: string;
}): Promise<{ tables: Record<string, TableData>; diff: ExecutePlanResponse["diff"]; newTables: string[] }> {
  const payload = {
    plan: opts.plan,
    tables: Object.values(opts.tables).map((t) => ({
      name: t.name,
      rows: t.rows,
      schema: t.schema
    }))
  };

  const resp = await fetchWithTimeout(
    `${API_BASE}/api/execute-plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    },
    TIMEOUT_EXECUTE_MS,
    { operation: "execute_plan", traceId: opts.traceId }
  );

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
  return {
    tables: nextTables,
    diff: normalizeExecuteDiff(data.diff),
    newTables: data.newTables
  };
}

/** 基于后端 ProjectState 执行 Plan，并返回最新表状态。*/
export async function executeProjectPlanById(opts: {
  projectId: string;
  plan: Plan;
  traceId?: string;
}): Promise<{ tables: Record<string, TableData>; diff: ExecutePlanResponse["diff"]; newTables: string[] }> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/projects/${encodeURIComponent(opts.projectId)}/execute-plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: opts.plan })
    },
    TIMEOUT_EXECUTE_MS,
    { operation: "execute_project_plan_by_id", traceId: opts.traceId }
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
  return {
    tables: nextTables,
    diff: normalizeExecuteDiff(data.diff),
    newTables: data.newTables
  };
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
  traceId?: string;
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

  const resp = await fetchWithTimeout(url, undefined, TIMEOUT_DEFAULT_MS, {
    operation: "fetch_chat_history",
    traceId: opts?.traceId
  });
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
