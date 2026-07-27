/**
 * SSE variant of requestAgentProjectPlan — maps /api/agent-stream terminal events
 * to the same AgentProjectPlanResult shape as the sync /api/agent path.
 */
import { consumeAgentStream, type AgentStreamEvent } from "./agentStream";
import {
  buildAgentProjectPlanRequestBody,
  parseAgentPreviewRecord,
  parsePlanFromWire,
  resolveTableRefs,
  type AgentProjectPlanRequestOpts,
  type AgentProjectPlanResult
} from "./llm";

/** Map collected SSE events to AgentProjectPlanResult (last terminal event wins). */
export function mapAgentStreamEventsToResult(
  events: AgentStreamEvent[]
): AgentProjectPlanResult {
  const clarificationEv = [...events].reverse().find((e) => e.kind === "clarification");
  if (clarificationEv) {
    const d = clarificationEv.data;
    return {
      kind: "clarification",
      clarification: {
        question: String(d.question ?? ""),
        options: (d.options as string[] | null | undefined) ?? null,
        context: (d.context as string | null | undefined) ?? null
      }
    };
  }

  const previewEv = [...events].reverse().find((e) => e.kind === "preview_ready");
  if (previewEv) {
    const d = previewEv.data;
    const histRaw = (d.previewHistory as Record<string, unknown>[] | undefined) ?? [];
    const warningsRaw = d.warnings as string[] | undefined;
    return {
      kind: "preview_ready",
      plan: parsePlanFromWire(d.plan as Record<string, unknown>),
      preview: parseAgentPreviewRecord(d.preview as Record<string, unknown>),
      previewHistory: histRaw.map((r) => parseAgentPreviewRecord(r)),
      state: (d.state as Record<string, unknown>) ?? {},
      warnings: warningsRaw?.length ? warningsRaw : undefined,
      summary: (d.summary as string | null | undefined) ?? null
    };
  }

  const planEv = [...events].reverse().find((e) => e.kind === "plan_done");
  if (planEv) {
    return {
      kind: "plan",
      plan: parsePlanFromWire(planEv.data.plan as Record<string, unknown>)
    };
  }

  const finishEv = [...events].reverse().find((e) => e.kind === "finish");
  if (finishEv) {
    const reason = String(finishEv.data.reason ?? "unknown");
    throw new Error(`agent-stream finish: ${reason}`);
  }

  throw new Error("agent-stream ended without a terminal event");
}

export async function requestAgentProjectPlanViaStream(
  opts: AgentProjectPlanRequestOpts
): Promise<AgentProjectPlanResult> {
  const tableRefs = await resolveTableRefs(opts.tables, opts.signal);
  const body = buildAgentProjectPlanRequestBody(opts, tableRefs);
  const events = await consumeAgentStream({
    body,
    traceId: opts.traceId,
    sessionId: opts.sessionId,
    signal: opts.signal
  });
  return mapAgentStreamEventsToResult(events);
}
