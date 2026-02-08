import React, { useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";

import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";

import type { Diff, Plan, SchemaCol } from "./types";
import { applyPlan, inferSchema } from "./engine";
import { requestPlan } from "./llm";

const initialRows = [
  { name: " Alice ", email: "ALICE@EXAMPLE.COM", price: 12.5, quantity: 2, signup_date: "2025-11-01", phone: "0912-345-678" },
  { name: "Bob", email: "bob@example.com", price: 5, quantity: 7, signup_date: "2025/12/03", phone: "0912-000-111" },
  { name: "Cathy", email: "Cathy@Example.Com", price: 99.99, quantity: 1, signup_date: "Dec 5 2025", phone: "0912-999-888" }
];

function schemaToColDefs(schema: SchemaCol[]): ColDef[] {
  return schema.map((c) => ({
    field: c.key,
    editable: true,
    flex: 1,
    minWidth: 140
  }));
}

export default function App() {
  const clone = <T,>(v: T): T => {
    // structuredClone is available in modern browsers; fallback to JSON for demo data.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sc = (globalThis as any).structuredClone;
    if (typeof sc === "function") return sc(v);
    return JSON.parse(JSON.stringify(v));
  };
  const [rows, setRows] = useState<Record<string, any>[]>(initialRows);
  const [schema, setSchema] = useState<SchemaCol[]>(() => inferSchema(initialRows));
  const [history, setHistory] = useState<Array<{ rows: Record<string, any>[]; schema: SchemaCol[] }>>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [prompt, setPrompt] = useState("");
  const [modelSource, setModelSource] = useState<"cloud" | "local">("cloud");
  const [schemaExpanded, setSchemaExpanded] = useState(false);
  const [status, setStatus] = useState<string>("Ready");
  const promptRef = useRef<HTMLTextAreaElement>(null);

  const colDefs = useMemo(() => schemaToColDefs(schema), [schema]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) {
        e.preventDefault();
        promptRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function onGenerate() {
    setStatus(modelSource === "cloud" ? "Calling cloud LLM…" : "Calling local LLM…");
    try {
      const sampleRows = rows.slice(0, 10);
      const nextPlan = await requestPlan({ prompt, schema, sampleRows, modelSource });
      setPlan(nextPlan);

      // compute diff preview without committing
      const preview = applyPlan(rows, schema, nextPlan);
      setDiff(preview.diff);

      setStatus("Plan generated. Review Diff, then Apply.");
    } catch (e: any) {
      setStatus("Error: " + String(e?.message ?? e));
    }
  }

  function onUndo() {
    setHistory((h) => {
      if (h.length === 0) {
        setStatus("Nothing to undo.");
        return h;
      }
      const last = h[h.length - 1];
      setRows(last.rows);
      setSchema(last.schema);
      setStatus("Undone last apply.");
      return h.slice(0, -1);
    });
  }

  function onApply() {
    if (!plan) return;
    // snapshot for undo
    setHistory((h) => [...h, { rows: clone(rows), schema: clone(schema) }]);
    const out = applyPlan(rows, schema, plan);
    setRows(out.rows);
    setSchema(out.schema);
    setStatus("Applied.");
    setPrompt("");
    setPlan(null);
    setDiff(null);
  }

  return (
    <>
      <div className="header">
        <div style={{ fontWeight: 600 }}>Cursor for Spreadsheet — MVP</div>
        <div className="small">
          Press <span className="kbd">Cmd</span>+<span className="kbd">K</span> to focus AI panel
        </div>
        <button className="btn" onClick={onUndo} disabled={history.length === 0}>Undo</button>
        <div style={{ marginLeft: "auto" }} className="small">{status}</div>
      </div>

      <div className="container">
        <div className="grid ag-theme-quartz">
          <AgGridReact
            rowData={rows}
            columnDefs={colDefs}
            defaultColDef={{ resizable: true, sortable: true, filter: true }}
            onCellValueChanged={(e) => {
              const idx = e.rowIndex!;
              const next = [...rows];
              next[idx] = { ...next[idx], [e.colDef.field!]: e.newValue };
              setRows(next);
            }}
          />
        </div>

        <div className="side-panel">
          <div className="panel-section ai-panel">
            <div style={{ fontWeight: 600, marginBottom: 8 }}>AI Edit</div>
            <div className="model-switch">
              <label>
                <input
                  type="radio"
                  name="modelSource"
                  checked={modelSource === "cloud"}
                  onChange={() => setModelSource("cloud")}
                />
                云端
              </label>
              <label>
                <input
                  type="radio"
                  name="modelSource"
                  checked={modelSource === "local"}
                  onChange={() => setModelSource("local")}
                />
                本地 (qwen2.5:7b)
              </label>
            </div>
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder='Try: "Add a column total_price = price * quantity"'
            />

            <div className="row">
              <button className="btn primary" onClick={onGenerate}>Generate Plan</button>
            </div>

            {plan && (
              <>
                <div style={{ fontWeight: 600 }}>Plan (LLM output)</div>
                <pre>{JSON.stringify(plan, null, 2)}</pre>
              </>
            )}

            {diff && (
              <>
                <div style={{ fontWeight: 600 }}>Diff Preview</div>
                <pre>{JSON.stringify(diff, null, 2)}</pre>
                <div className="row">
                  <button className="btn primary" onClick={onApply}>Apply</button>
                  <div className="small">Applies the steps to the grid data.</div>
                </div>
              </>
            )}
          </div>

          <div className="panel-section schema-section">
            <button
              type="button"
              className="schema-toggle"
              onClick={() => setSchemaExpanded((v) => !v)}
            >
              <span className="schema-toggle-icon">{schemaExpanded ? "▼" : "▶"}</span>
              Schema
            </button>
            {schemaExpanded && (
              <>
                <pre>{JSON.stringify(schema, null, 2)}</pre>
                <div className="small">This schema + sample rows are what the LLM sees.</div>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
