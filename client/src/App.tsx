import React, { useEffect, useMemo, useState } from "react";
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
  const [rows, setRows] = useState<Record<string, any>[]>(initialRows);
  const [schema, setSchema] = useState<SchemaCol[]>(() => inferSchema(initialRows));
  const [plan, setPlan] = useState<Plan | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [isModalOpen, setModalOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<string>("Ready");

  const colDefs = useMemo(() => schemaToColDefs(schema), [schema]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) {
        e.preventDefault();
        setModalOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function onGenerate() {
    setStatus("Calling LLM…");
    try {
      const sampleRows = rows.slice(0, 10);
      const nextPlan = await requestPlan({ prompt, schema, sampleRows });
      setPlan(nextPlan);

      // compute diff preview without committing
      const preview = applyPlan(rows, schema, nextPlan);
      setDiff(preview.diff);

      setStatus("Plan generated. Review Diff, then Apply.");
    } catch (e: any) {
      setStatus("Error: " + String(e?.message ?? e));
    }
  }

  function onApply() {
    if (!plan) return;
    const out = applyPlan(rows, schema, plan);
    setRows(out.rows);
    setSchema(out.schema);
    setStatus("Applied.");
    setModalOpen(false);
    setPrompt("");
    setPlan(null);
    setDiff(null);
  }

  function onClose() {
    setModalOpen(false);
    setPlan(null);
    setDiff(null);
  }

  return (
    <>
      <div className="header">
        <div style={{ fontWeight: 600 }}>Cursor for Spreadsheet — MVP</div>
        <div className="small">
          Press <span className="kbd">Cmd</span>+<span className="kbd">K</span> to AI-edit the grid
        </div>
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

        <div className="panel">
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Schema</div>
          <pre>{JSON.stringify(schema, null, 2)}</pre>
          <div className="small">This schema + sample rows are what the LLM sees.</div>
        </div>
      </div>

      {isModalOpen && (
        <div className="modal-backdrop" onMouseDown={onClose}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <header>
              <div style={{ fontWeight: 600 }}>Cmd+K — AI Edit</div>
              <button className="btn" onClick={onClose}>Close</button>
            </header>
            <main>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder='Try: "Add a column total_price = price * quantity"'
              />

              <div className="row">
                <button className="btn primary" onClick={onGenerate}>Generate Plan</button>
                <div className="small">Backend: http://localhost:8787</div>
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
            </main>
          </div>
        </div>
      )}
    </>
  );
}
