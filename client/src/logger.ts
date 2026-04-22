/**
 * 前端控制台日志：统一事件形状，便于与后端 STDOUT 通过 traceId 对齐排查。
 *
 * - sessionId：页面生命周期内固定。
 * - traceId：单次 HTTP 请求生成或由调用方传入，对应请求头 X-Request-ID。
 */

const DEV = import.meta.env.DEV;
/** 设为 "0" 可在开发环境关闭 info/debug。 */
const FORCE_DISABLE =
  import.meta.env.VITE_ENABLE_CONSOLE_LOG === "0";

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

const SESSION_ID = newId();

export function getSessionId(): string {
  return SESSION_ID;
}

export function generateTraceId(): string {
  return newId();
}

export type AppLogPayload = Record<string, unknown>;

function emit(
  level: "info" | "error" | "debug",
  event: string,
  payload?: AppLogPayload
): void {
  const entry = {
    level,
    event,
    sessionId: SESSION_ID,
    ts: new Date().toISOString(),
    ...payload
  };
  if (level === "error") {
    console.error("[APP]", entry);
    return;
  }
  if (level === "debug" && (!DEV || FORCE_DISABLE)) {
    return;
  }
  if (level === "info" && (!DEV || FORCE_DISABLE)) {
    return;
  }
  console.log("[APP]", entry);
}

/**
 * 记录一般业务事件（开发环境默认开启）。
 *
 * @param event 事件名，如 cmdk_open。
 * @param payload 附加字段，勿写入完整 prompt 原文。
 */
export function logInfo(event: string, payload?: AppLogPayload): void {
  emit("info", event, payload);
}

/**
 * 记录错误（各环境均输出）。
 *
 * @param event 事件名。
 * @param payload 附加字段。
 */
export function logError(event: string, payload?: AppLogPayload): void {
  emit("error", event, payload);
}

/**
 * 详细调试信息，仅开发环境且未禁用开关时输出。
 *
 * @param event 事件名。
 * @param payload 附加字段。
 */
export function logDebug(event: string, payload?: AppLogPayload): void {
  emit("debug", event, payload);
}
