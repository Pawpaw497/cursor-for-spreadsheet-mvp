import { z } from "zod";
import type { AgentRequestContext } from "./agentContext";
import {
  generateTraceId,
  logError,
  logInfo
} from "./logger";
import type { Diff, Plan, PreviewRecord, SchemaCol, TableData } from "./types";

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

/** 与后端 ``ExecuteTable`` 对齐：``model_dump()`` 可能得到 ``schema_`` 而非 ``schema``。 */
type ExecuteTableWire = {
  name: string;
  rows: Record<string, any>[];
  schema?: SchemaCol[];
  schema_?: SchemaCol[];
};

function schemaFromExecuteTableWire(t: ExecuteTableWire): SchemaCol[] {
  return (t.schema ?? t.schema_ ?? []) as SchemaCol[];
}

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

export type ModelSource = "cloud" | "local";

export type ModelOption = { id: string; label: string };

export type ConfigResponse = {
  /** 本次 uvicorn/FastAPI 进程启动 ID；前端气泡对话仅在此 session 内保留。 */
  serverBootId?: string;
  openRouterModel: string;
  openRouterModels: ModelOption[];
  ollamaModel: string;
  ollamaModels: ModelOption[];
  /** 后端各上游 LLM HTTP 调用的最大秒数（与 `server/app/services/llm.py` 一致）。 */
  llmUpstreamMaxTimeoutSeconds?: number;
  /** 建议的前端 LLM 请求总超时（毫秒）= 上游最大 + 缓冲；由 `/api/config` 下发。 */
  llmClientTimeoutRecommendedMs?: number;
  /** Stage 6: optional SQLite session backup when `SESSION_MEMORY_DB_ENABLED=1`. */
  sessionMemoryEnabled?: boolean;
  sessionMemoryTtlDays?: number;
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
  meta?: {
    modelSource?: ModelSource;
    modelId?: string | null;
    modelTag?: string;
    [key: string]: unknown;
  };
};

const API_BASE = "http://localhost:8787";

const TIMEOUT_DEFAULT_MS = 8000;
/**
 * 与后端 `recommended_llm_client_timeout_ms()` 在默认常量下一致：
 * max(60, 90, 120)s + 30s 缓冲 → 150s。在成功拉取 `/api/config` 前作为 LLM 请求超时。
 */
const FALLBACK_LLM_CLIENT_TIMEOUT_MS = 150_000;

let configuredLlmClientTimeoutMs: number | null = null;

/** 当前用于 `/api/agent*` 等 LLM 调用的客户端超时（毫秒）。 */
export function getLlmClientTimeoutMs(): number {
  return configuredLlmClientTimeoutMs ?? FALLBACK_LLM_CLIENT_TIMEOUT_MS;
}
const TIMEOUT_IMPORT_MS = 20000;
const TIMEOUT_EXECUTE_MS = 120000;
const TIMEOUT_EXPORT_MS = 60000;

/**
 * Agent ``previewTables`` 每张表最大行数（须与后端
 * ``app.services.agent_preview.PREVIEW_TABLES_MAX_ROWS_PER_TABLE`` 一致）。
 */
export const PREVIEW_TABLES_MAX_ROWS_PER_TABLE = 5000;

type FetchLogOptions = {
  /** 用于控制台与后端对齐的请求关联 ID。 */
  traceId?: string;
  /** 工作区会话 ID（``WorkspaceMemory.sessionMeta.sessionId``），对应请求头 X-Session-ID。 */
  sessionId?: string;
  /** 业务操作名，如 request_plan。 */
  operation: string;
};

/**
 * 将可选的调用方 ``AbortSignal`` 与基于 ``timeoutMs`` 的超时控制器合并；
 * 任一 abort 则合成 signal 进入 aborted 状态。
 *
 * @returns ``armTimeout`` 须在发起 ``fetch`` 前调用；``disarm`` 在 ``finally`` 中调用以清理定时器与监听器。
 */
export function combinedAbortSignalForFetch(
  timeoutMs: number,
  userSignal?: AbortSignal
): {
  signal: AbortSignal;
  armTimeout: () => void;
  disarm: () => void;
} {
  const timeoutController = new AbortController();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  let disposables: (() => void) | undefined;

  const armTimeout = () => {
    timeoutId = setTimeout(() => timeoutController.abort(), timeoutMs);
  };

  const disarm = () => {
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
      timeoutId = undefined;
    }
    disposables?.();
    disposables = undefined;
  };

  if (!userSignal) {
    return { signal: timeoutController.signal, armTimeout, disarm };
  }

  const anyFn = (
    AbortSignal as unknown as { any?: (signals: AbortSignal[]) => AbortSignal }
  ).any;
  if (typeof anyFn === "function") {
    return {
      signal: anyFn([userSignal, timeoutController.signal]),
      armTimeout,
      disarm
    };
  }

  const merged = new AbortController();
  const forward = () => {
    merged.abort();
  };
  userSignal.addEventListener("abort", forward);
  timeoutController.signal.addEventListener("abort", forward);
  disposables = () => {
    userSignal.removeEventListener("abort", forward);
    timeoutController.signal.removeEventListener("abort", forward);
  };
  if (userSignal.aborted || timeoutController.signal.aborted) {
    merged.abort();
  }
  return { signal: merged.signal, armTimeout, disarm };
}

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

  const { signal: userSignal, ...restInit } = init ?? {};
  const headers = new Headers(restInit.headers);
  if (!headers.has("X-Request-ID")) {
    headers.set("X-Request-ID", traceId);
  }
  const sessionId = logOptions?.sessionId?.trim();
  if (sessionId && !headers.has("X-Session-ID")) {
    headers.set("X-Session-ID", sessionId);
  }

  logInfo("request_start", { operation, path, traceId, method });

  const abortCtl = combinedAbortSignalForFetch(timeoutMs, userSignal);
  abortCtl.armTimeout();
  const t0 = performance.now();
  try {
    const resp = await fetch(url, {
      ...restInit,
      headers,
      signal: abortCtl.signal
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
    const isAbort = err.name === "AbortError";
    const abortedByUser = Boolean(userSignal?.aborted);
    logError("request_error", {
      operation,
      path,
      traceId,
      isTimeout: isAbort && !abortedByUser,
      abortedByUser,
      durationMs,
      message: isAbort
        ? abortedByUser
          ? "请求已取消"
          : `请求超时（>${timeoutMs}ms），请检查后端是否在 ${API_BASE} 运行`
        : err.message
    });
    if (isAbort) {
      if (abortedByUser) {
        throw e;
      }
      throw new Error(
        `请求超时（>${timeoutMs}ms），请检查后端是否在 ${API_BASE} 运行`
      );
    }
    throw e;
  } finally {
    abortCtl.disarm();
  }
}

/** 从错误响应 body 解析可读信息（string detail 或 `{ reason }` 对象）。 */
export function parseApiErrorMessage(status: number, txt: string): string {
  try {
    const j = JSON.parse(txt) as {
      detail?: string | { reason?: string; staleReason?: string };
    };
    if (j && typeof j.detail === "string") return j.detail;
    if (
      j &&
      j.detail &&
      typeof j.detail === "object" &&
      typeof j.detail.reason === "string"
    ) {
      if (j.detail.reason === "stale_preview") {
        if (j.detail.staleReason === "content") {
          return `[${status}] Preview is stale: table data changed after preview. Regenerate preview.`;
        }
        if (j.detail.staleReason === "structure") {
          return `[${status}] Preview is stale: table structure changed after preview. Regenerate preview.`;
        }
      }
      return `[${status}] ${j.detail.reason}`;
    }
  } catch {
    // ignore
  }
  return txt ? `[${status}] ${txt}` : `[${status}] Request failed`;
}

/** 从错误响应的 body 中解析出可读信息；若为 JSON 且含 detail 则返回 detail（后端已含错误号），否则返回 "[status] 原始文本"。 */
function errorMessageFromResponse(resp: Response, txt: string): string {
  return parseApiErrorMessage(resp.status, txt);
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
  const data = (await resp.json()) as ConfigResponse;
  if (
    typeof data.llmClientTimeoutRecommendedMs === "number" &&
    Number.isFinite(data.llmClientTimeoutRecommendedMs) &&
    data.llmClientTimeoutRecommendedMs >= 10_000
  ) {
    configuredLlmClientTimeoutMs = Math.floor(data.llmClientTimeoutRecommendedMs);
  }
  return data;
}

export function parseAgentPreviewRecord(raw: Record<string, unknown>): PreviewRecord {
  const planRaw = raw.plan as Record<string, unknown>;
  return {
    id: String(raw.id),
    plan: PlanSchema.parse(planRaw),
    diff: normalizeExecuteDiff(raw.diff as ExecutePlanResponse["diff"] | undefined),
    newTables: (raw.newTables as string[] | undefined) ?? (raw.new_tables as string[] | undefined) ?? [],
    status: (raw.status as PreviewRecord["status"]) ?? "pending",
    user_decision:
      (raw.user_decision as PreviewRecord["user_decision"]) ??
      (raw.userDecision as PreviewRecord["user_decision"]) ??
      null,
    user_decision_reason:
      (raw.user_decision_reason as string | null | undefined) ??
      (raw.userDecisionReason as string | null | undefined) ??
      null,
    execution_error:
      (raw.execution_error as string | null | undefined) ??
      (raw.executionError as string | null | undefined) ??
      null,
    tables_fingerprint_at_preview: String(
      raw.tables_fingerprint_at_preview ?? raw.tablesFingerprintAtPreview ?? ""
    ),
    created_at: Number(raw.created_at ?? raw.createdAt ?? 0),
    resolved_at:
      raw.resolved_at != null
        ? Number(raw.resolved_at)
        : raw.resolvedAt != null
          ? Number(raw.resolvedAt)
          : null
  };
}

export type AgentProjectPlanResult =
  | { kind: "plan"; plan: Plan }
  | {
      kind: "preview_ready";
      plan: Plan;
      preview: PreviewRecord;
      previewHistory: PreviewRecord[];
      state: Record<string, unknown>;
      warnings?: string[];
      /**
       * AI 生成的 Diff 自然语言摘要；异步生成，首包恒为 null/undefined
       * （见 .cursor/plans/agent-evolution-p1-v2-core.plan.md，p1-preview-summary-wire）。
       * 渲染时需搭配免责声明，见 previewSummaryDisclaimer()。
       */
      summary?: string | null;
    }
  | {
      kind: "committed";
      executeResult: {
        tables: Record<string, TableData>;
        diff: Diff;
        newTables: string[];
      };
      previewHistory: PreviewRecord[];
    }
  | { kind: "preview_aborted"; previewHistory: PreviewRecord[] }
  | {
      kind: "clarification";
      clarification: { question: string; options?: string[] | null; context?: string | null };
    };

export type AgentProjectPlanRequestOpts = {
  prompt: string;
  tables: TableData[];
  modelSource?: ModelSource;
  cloudModelId?: string;
  localModelId?: string;
  traceId?: string;
  sessionId?: string;
  history?: { role: "user" | "assistant"; content: string }[];
  appliedPlansSummary?: string;
  previewLifecycle?: boolean;
  projectId?: string | null;
  previewTables?: TableData[];
  previewHistory?: PreviewRecord[];
  revisionCount?: number;
  previewDecision?: "confirm" | "abort" | "revise";
  previewId?: string | null;
  revisionMessage?: string | null;
  commitPlan?: Plan | null;
  clarificationReply?: string | null;
  clarificationTurnId?: string | null;
  /** 与超时合成后任一 abort 即取消本次 ``fetch``（例如连续提交时取消上一次 in-flight 请求）。 */
  signal?: AbortSignal;
  context?: AgentRequestContext;
};

export function parsePlanFromWire(raw: Record<string, unknown>): Plan {
  return PlanSchema.parse(raw);
}

/** Shared JSON body for ``/api/agent`` and ``/api/agent-stream``. */
export function buildAgentProjectPlanRequestBody(
  opts: AgentProjectPlanRequestOpts,
  tableRefs: Record<string, string>
): Record<string, unknown> {
  const tablesPayload = opts.tables.map((t) => ({
    name: t.name,
    schema: t.schema,
    tableRef: tableRefs[t.name]
  }));
  const previewTablesPayload =
    opts.previewTables?.map((t) => ({
      name: t.name,
      rows: t.rows.slice(0, PREVIEW_TABLES_MAX_ROWS_PER_TABLE),
      schema: t.schema
    })) ?? undefined;
  const previewHistoryPayload = (opts.previewHistory ?? []).map((p) => ({
    id: p.id,
    plan: p.plan,
    diff: p.diff,
    newTables: p.newTables,
    status: p.status,
    user_decision: p.user_decision ?? undefined,
    user_decision_reason: p.user_decision_reason ?? undefined,
    execution_error: p.execution_error ?? undefined,
    tables_fingerprint_at_preview: p.tables_fingerprint_at_preview ?? "",
    created_at: p.created_at,
    resolved_at: p.resolved_at ?? undefined
  }));
  const body: Record<string, unknown> = {
    prompt: opts.prompt,
    tables: tablesPayload,
    modelSource: opts.modelSource ?? "cloud",
    cloudModelId: opts.cloudModelId ?? undefined,
    localModelId: opts.localModelId ?? undefined,
    history: opts.history ?? [],
    appliedPlansSummary: opts.appliedPlansSummary,
    previewLifecycle: opts.previewLifecycle ?? false,
    revisionCount: opts.revisionCount ?? 0,
    previewHistory: previewHistoryPayload
  };
  if (opts.projectId) {
    body.projectId = opts.projectId;
  }
  if (previewTablesPayload) {
    body.previewTables = previewTablesPayload;
  }
  if (opts.previewDecision) {
    body.previewDecision = opts.previewDecision;
  }
  if (opts.previewId) {
    body.previewId = opts.previewId;
  }
  if (opts.revisionMessage) {
    body.revisionMessage = opts.revisionMessage;
  }
  if (opts.commitPlan) {
    body.commitPlan = opts.commitPlan;
  }
  if (opts.context) {
    body.context = opts.context;
  }
  if (opts.clarificationReply) {
    body.clarificationReply = opts.clarificationReply;
  }
  if (opts.clarificationTurnId) {
    body.clarificationTurnId = opts.clarificationTurnId;
  }
  return body;
}

async function contentHash(t: TableData): Promise<string> {
  const payload = JSON.stringify({ name: t.name, schema: t.schema, rows: t.rows });
  const data = new TextEncoder().encode(payload);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function uploadTableForAgent(
  t: TableData,
  signal?: AbortSignal
): Promise<string> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/data/upload`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Client-Request-Id": await contentHash(t)
      },
      body: JSON.stringify({ name: t.name, schema: t.schema, rows: t.rows }),
      signal
    },
    TIMEOUT_IMPORT_MS
  );
  if (!resp.ok) {
    throw new Error(errorMessageFromResponse(resp, await resp.text()));
  }
  const data = (await resp.json()) as { tableId: string };
  return data.tableId;
}

/** 并发上传所有表，返回 name → tableId 映射；两个 transport 共用。 */
export async function resolveTableRefs(
  tables: TableData[],
  signal?: AbortSignal
): Promise<Record<string, string>> {
  const refs: Record<string, string> = {};
  if (tables.length === 0) {
    return refs;
  }
  await Promise.all(
    tables.map(async (t) => {
      refs[t.name] = await uploadTableForAgent(t, signal);
    })
  );
  return refs;
}

export async function requestAgentProjectPlan(
  opts: AgentProjectPlanRequestOpts
): Promise<AgentProjectPlanResult> {
  const tableRefs = await resolveTableRefs(opts.tables, opts.signal);
  const body = buildAgentProjectPlanRequestBody(opts, tableRefs);
  const resp = await fetchWithTimeout(
    `${API_BASE}/api/agent`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: opts.signal
    },
    getLlmClientTimeoutMs(),
    {
      operation: "request_agent_project_plan",
      traceId: opts.traceId,
      sessionId: opts.sessionId
    }
  );
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(errorMessageFromResponse(resp, txt));
  }
  const data = (await resp.json()) as Record<string, unknown>;
  const kind = String(data.kind ?? "");
  if (kind === "preview_ready") {
    const previewRaw = data.preview as Record<string, unknown>;
    const histRaw = (data.previewHistory as Record<string, unknown>[] | undefined) ?? [];
    const warningsRaw = data.warnings as string[] | undefined;
    return {
      kind: "preview_ready",
      plan: PlanSchema.parse(data.plan as Record<string, unknown>),
      preview: parseAgentPreviewRecord(previewRaw),
      previewHistory: histRaw.map((r) => parseAgentPreviewRecord(r)),
      state: (data.state as Record<string, unknown>) ?? {},
      warnings: warningsRaw?.length ? warningsRaw : undefined,
      summary: (data.summary as string | null | undefined) ?? null
    };
  }
  if (kind === "committed") {
    const exec = data.executeResult as ExecutePlanResponse;
    const histRaw = (data.previewHistory as Record<string, unknown>[] | undefined) ?? [];
    const nextTables: Record<string, TableData> = {};
    for (const [name, t] of Object.entries(exec.tables)) {
      const tw = t as ExecuteTableWire;
      nextTables[name] = {
        name: tw.name,
        rows: tw.rows,
        schema: schemaFromExecuteTableWire(tw)
      };
    }
    return {
      kind: "committed",
      executeResult: {
        tables: nextTables,
        diff: normalizeExecuteDiff(exec.diff),
        newTables: exec.newTables ?? []
      },
      previewHistory: histRaw.map((r) => parseAgentPreviewRecord(r))
    };
  }
  if (kind === "preview_aborted") {
    const histRaw = (data.previewHistory as Record<string, unknown>[] | undefined) ?? [];
    return {
      kind: "preview_aborted",
      previewHistory: histRaw.map((r) => parseAgentPreviewRecord(r))
    };
  }
  if (kind === "clarification") {
    const c = data.clarification as {
      question: string;
      options?: string[] | null;
      context?: string | null;
    };
    return { kind: "clarification", clarification: c };
  }
  if (data.plan) {
    return { kind: "plan", plan: PlanSchema.parse(data.plan as Record<string, unknown>) };
  }
  throw new Error("Unexpected /api/agent response shape");
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
    const tw = t as ExecuteTableWire;
    nextTables[name] = {
      name: tw.name,
      rows: tw.rows,
      schema: schemaFromExecuteTableWire(tw)
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
    const tw = t as ExecuteTableWire;
    nextTables[name] = {
      name: tw.name,
      rows: tw.rows,
      schema: schemaFromExecuteTableWire(tw)
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
