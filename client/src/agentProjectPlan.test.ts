import { afterEach, describe, expect, it, vi } from "vitest";

import {
  mapAgentStreamEventsToResult,
  requestAgentProjectPlanViaStream
} from "./agentProjectPlan";
import type { AgentStreamEvent } from "./agentStream";

function sseReadableStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    }
  });
}

describe("mapAgentStreamEventsToResult", () => {
  it("maps clarification terminal event", () => {
    const events: AgentStreamEvent[] = [
      {
        kind: "clarification",
        data: {
          question: "Which table?",
          options: ["Sheet1", "Sheet2"],
          context: "Ambiguous add_column"
        }
      }
    ];
    const result = mapAgentStreamEventsToResult(events);
    expect(result.kind).toBe("clarification");
    if (result.kind === "clarification") {
      expect(result.clarification.question).toBe("Which table?");
      expect(result.clarification.options).toEqual(["Sheet1", "Sheet2"]);
      expect(result.clarification.context).toBe("Ambiguous add_column");
    }
  });

  it("prefers preview_ready over plan_done", () => {
    const plan = {
      intent: "t",
      steps: [{ action: "add_column", name: "c", expression: "1" }]
    };
    const events: AgentStreamEvent[] = [
      {
        kind: "preview_ready",
        data: {
          plan,
          preview: {
            id: "pv1",
            plan,
            diff: {
              addedColumns: ["c"],
              modifiedColumns: [],
              validationWarnings: [],
              validationErrors: []
            },
            newTables: [],
            status: "pending",
            created_at: 1
          },
          state: { revision_count: 0 }
        }
      },
      { kind: "plan_done", data: { plan, state: {} } }
    ];
    const result = mapAgentStreamEventsToResult(events);
    expect(result.kind).toBe("preview_ready");
  });

  it("preserves previewHistory from preview_ready SSE payload", () => {
    const plan = {
      intent: "t",
      steps: [{ action: "add_column", name: "c", expression: "1" }]
    };
    const priorPreview = {
      id: "pv0",
      plan,
      diff: {
        addedColumns: [],
        modifiedColumns: [],
        validationWarnings: [],
        validationErrors: []
      },
      new_tables: [],
      status: "revised",
      created_at: 0
    };
    const currentPreview = {
      id: "pv1",
      plan,
      diff: {
        addedColumns: ["c"],
        modifiedColumns: [],
        validationWarnings: [],
        validationErrors: []
      },
      new_tables: [],
      status: "pending",
      created_at: 1
    };
    const result = mapAgentStreamEventsToResult([
      {
        kind: "preview_ready",
        data: {
          plan,
          preview: currentPreview,
          previewHistory: [priorPreview, currentPreview],
          state: { revision_count: 1 }
        }
      },
      { kind: "plan_done", data: { plan, state: {} } }
    ]);
    expect(result.kind).toBe("preview_ready");
    if (result.kind === "preview_ready") {
      expect(result.previewHistory).toHaveLength(2);
      expect(result.previewHistory[0].id).toBe("pv0");
      expect(result.previewHistory[0].status).toBe("revised");
      expect(result.previewHistory[1].id).toBe("pv1");
      expect(result.preview.id).toBe("pv1");
    }
  });

  it("defaults summary to null when absent (p1-preview-summary-wire首包)", () => {
    const plan = {
      intent: "t",
      steps: [{ action: "add_column", name: "c", expression: "1" }]
    };
    const result = mapAgentStreamEventsToResult([
      {
        kind: "preview_ready",
        data: {
          plan,
          preview: {
            id: "pv1",
            plan,
            diff: {
              addedColumns: ["c"],
              modifiedColumns: [],
              validationWarnings: [],
              validationErrors: []
            },
            newTables: [],
            status: "pending",
            created_at: 1
          },
          state: {}
        }
      }
    ]);
    expect(result.kind).toBe("preview_ready");
    if (result.kind === "preview_ready") {
      expect(result.summary).toBeNull();
    }
  });

  it("passes through a populated summary field", () => {
    const plan = {
      intent: "t",
      steps: [{ action: "add_column", name: "c", expression: "1" }]
    };
    const result = mapAgentStreamEventsToResult([
      {
        kind: "preview_ready",
        data: {
          plan,
          preview: {
            id: "pv1",
            plan,
            diff: {
              addedColumns: ["c"],
              modifiedColumns: [],
              validationWarnings: [],
              validationErrors: []
            },
            newTables: [],
            status: "pending",
            created_at: 1
          },
          state: {},
          summary: "Added column c."
        }
      }
    ]);
    expect(result.kind).toBe("preview_ready");
    if (result.kind === "preview_ready") {
      expect(result.summary).toBe("Added column c.");
    }
  });

  it("maps plan_done to plan result", () => {
    const plan = {
      intent: "t",
      steps: [{ action: "add_column", name: "c", expression: "1" }]
    };
    const result = mapAgentStreamEventsToResult([{ kind: "plan_done", data: { plan } }]);
    expect(result.kind).toBe("plan");
    if (result.kind === "plan") {
      expect(result.plan.steps).toHaveLength(1);
    }
  });

  it("throws on finish without plan", () => {
    expect(() =>
      mapAgentStreamEventsToResult([{ kind: "finish", data: { reason: "max_turns" } }])
    ).toThrow(/agent-stream finish: max_turns/);
  });
});

describe("requestAgentProjectPlanViaStream", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("consumes SSE clarification and maps to result kind", async () => {
    const sse =
      'event: clarification\ndata: {"question":"Which?","options":["A"],"context":"ctx"}\n\n';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        body: sseReadableStream([sse])
      })
    );

    const result = await requestAgentProjectPlanViaStream({
      prompt: "add column",
      tables: []
    });

    expect(result.kind).toBe("clarification");
    if (result.kind === "clarification") {
      expect(result.clarification.question).toBe("Which?");
      expect(result.clarification.options).toEqual(["A"]);
    }
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8787/api/agent-stream",
      expect.objectContaining({ method: "POST" })
    );
  });
});
