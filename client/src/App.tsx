import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import type { GridApi } from "ag-grid-community";

import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";

import type { CellFormat, Diff, Plan, SchemaCol, TableData } from "./types";
import { applyPlan, applyProjectPlan, inferSchema } from "./engine";
import { exportProjectToExcel, fetchConfig, requestPlan, requestProjectPlan } from "./llm";
import type { ConfigResponse, ModelOption } from "./llm";

type ConversationEntry = {
  id: number;
  prompt: string;
  payload: any;
  plan: Plan | null;
  diff: Diff | null;
  createdAt: string;
  modelSource: "cloud" | "local";
  modelId: string | null;
};

const initialRows = [
  { name: " Alice ", email: "ALICE@EXAMPLE.COM", price: 12.5, quantity: 2, signup_date: "2025-11-01", phone: "0912-345-678" },
  { name: "Bob", email: "bob@example.com", price: 5, quantity: 7, signup_date: "2025/12/03", phone: "0912-000-111" },
  { name: "Cathy", email: "Cathy@Example.Com", price: 99.99, quantity: 1, signup_date: "Dec 5 2025", phone: "0912-999-888" }
];

const secondTableRows = [
  { order_id: "O1", customer: "Alice", amount: 25 },
  { order_id: "O2", customer: "Bob", amount: 35 },
  { order_id: "O3", customer: "Cathy", amount: 99.99 }
];

/** Excel 风格列名：0→A, 1→B, …, 26→AA */
function indexToCol(i: number): string {
  if (i < 26) return String.fromCharCode(65 + i);
  return indexToCol(Math.floor(i / 26) - 1) + indexToCol(i % 26);
}

function createTable(name: string, rows: Record<string, any>[]): TableData {
  return {
    name,
    rows: [...rows],
    schema: inferSchema(rows)
  };
}

function formatToCellStyle(fmt: CellFormat): React.CSSProperties {
  const s: React.CSSProperties = {};
  if (fmt.bold) s.fontWeight = "bold";
  if (fmt.italic) s.fontStyle = "italic";
  if (fmt.underline) s.textDecoration = "underline";
  if (fmt.fontFamily) s.fontFamily = fmt.fontFamily;
  if (fmt.fontSize) s.fontSize = `${fmt.fontSize}px`;
  if (fmt.textAlign) s.textAlign = fmt.textAlign;
  if (fmt.backgroundColor) s.backgroundColor = fmt.backgroundColor;
  return s;
}

const ROW_NUM_COL: ColDef = {
  headerName: "",
  colId: "__rowNum",
  width: 48,
  maxWidth: 48,
  resizable: false,
  sortable: false,
  filter: false,
  editable: false,
  pinned: "left",
  valueGetter: (params) => (params.node?.rowIndex ?? 0) + 1,
  cellStyle: { backgroundColor: "#e8e8e8", color: "#666" },
  headerClass: "row-num-header"
};

/** 可双击编辑、回车保存的列头 */
function EditableHeader(
  props: {
    displayName: string;
    onSave: (newName: string) => void;
  } & Record<string, unknown>
) {
  const { displayName, onSave } = props;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(displayName);
  const inputRef = useRef<HTMLInputElement>(null);

  const commit = useCallback(() => {
    setEditing(false);
    const trimmed = value.trim();
    if (trimmed && trimmed !== displayName) onSave(trimmed);
    else setValue(displayName);
  }, [value, displayName, onSave]);

  useEffect(() => {
    if (editing) {
      setValue(displayName);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing, displayName]);

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="editable-header-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {
            setEditing(false);
            setValue(displayName);
          }
        }}
        onClick={(e) => e.stopPropagation()}
      />
    );
  }
  return (
    <span
      className="editable-header-label"
      onDoubleClick={(e) => {
        e.stopPropagation();
        setEditing(true);
      }}
    >
      {displayName}
    </span>
  );
}

function schemaToColDefs(
  schema: SchemaCol[],
  activeTable: string,
  cellFormats: Record<string, CellFormat>,
  onRenameColumn: (oldKey: string, newKey: string) => void
): ColDef[] {
  const dataCols: ColDef[] = schema.map((c) => ({
    field: c.key,
    headerName: c.key,
    editable: true,
    flex: 1,
    minWidth: 140,
    headerComponent: EditableHeader,
    headerComponentParams: {
      displayName: c.key,
      onSave: (newKey: string) => onRenameColumn(c.key, newKey)
    },
    cellStyle: (params) => {
      const rowIndex = params.rowIndex ?? params.node?.rowIndex ?? undefined;
      const colId = params.colDef?.field ?? c.key;
      if (rowIndex == null) return undefined;
      const key = `${activeTable}:${rowIndex}:${colId}`;
      const fmt = cellFormats[key];
      return fmt ? (formatToCellStyle(fmt) as Record<string, string | number>) : undefined;
    }
  }));
  return [ROW_NUM_COL, ...dataCols];
}

export default function App() {
  const clone = <T,>(v: T): T => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sc = (globalThis as any).structuredClone;
    if (typeof sc === "function") return sc(v);
    return JSON.parse(JSON.stringify(v));
  };

  const [tables, setTables] = useState<Record<string, TableData>>(() => ({
    Sheet1: createTable("Sheet1", initialRows),
    Orders: createTable("Orders", secondTableRows)
  }));
  const [activeTable, setActiveTable] = useState<string>("Sheet1");
  const [history, setHistory] = useState<Array<Record<string, TableData>>>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [newTablesPreview, setNewTablesPreview] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [modelSource, setModelSource] = useState<"cloud" | "local">("cloud");
  const [modelOptions, setModelOptions] = useState<ConfigResponse | null>(null);
  const [cloudModelId, setCloudModelId] = useState<string>("");
  const [localModelId, setLocalModelId] = useState<string>("");
  const [schemaExpanded, setSchemaExpanded] = useState(false);
  const [status, setStatus] = useState<string>("Ready");
  const [aiPanelCollapsed, setAiPanelCollapsed] = useState(false);
  const [cellFormats, setCellFormats] = useState<Record<string, CellFormat>>({});
  const [toolbarFont, setToolbarFont] = useState("system-ui");
  const [toolbarFontSize, setToolbarFontSize] = useState(12);
  const [conversations, setConversations] = useState<ConversationEntry[]>([]);
  const [expandedPayloadIds, setExpandedPayloadIds] = useState<Set<number>>(
    () => new Set()
  );
  const [expandedResponseIds, setExpandedResponseIds] = useState<Set<number>>(
    () => new Set()
  );
  const [expandedDiffIds, setExpandedDiffIds] = useState<Set<number>>(
    () => new Set()
  );
  const [diffExpanded, setDiffExpanded] = useState(false);
  const [activeAiTab, setActiveAiTab] = useState<"chat" | "history">("chat");
  const gridRef = useRef<GridApi | null>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);

  const tableNames = Object.keys(tables);
  const currentTable = tables[activeTable];
  const onRenameColumn = useCallback((oldKey: string, newKey: string) => {
    if (!currentTable || newKey.trim() === "" || newKey === oldKey) return;
    const { schema, rows } = currentTable;
    if (schema.every((c) => c.key !== oldKey)) return;
    const nextSchema = schema.map((c) => (c.key === oldKey ? { ...c, key: newKey } : c));
    const nextRows = rows.map((r) => {
      const { [oldKey]: v, ...rest } = r;
      return { ...rest, [newKey]: v };
    });
    setTables((prev) => ({
      ...prev,
      [activeTable]: { name: activeTable, rows: nextRows, schema: nextSchema }
    }));
    setCellFormats((prev) => {
      const next: Record<string, CellFormat> = {};
      for (const k of Object.keys(prev)) {
        const m = k.match(new RegExp(`^(.+):(\\d+):${oldKey.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
        if (m) next[`${m[1]}:${m[2]}:${newKey}`] = prev[k];
        else next[k] = prev[k];
      }
      return next;
    });
  }, [activeTable, currentTable]);

  const colDefs = useMemo(
    () =>
      currentTable
        ? schemaToColDefs(currentTable.schema, activeTable, cellFormats, onRenameColumn)
        : [],
    [currentTable, activeTable, cellFormats, onRenameColumn]
  );

  const refreshCells = useCallback(() => {
    gridRef.current?.refreshCells({ force: true });
  }, []);

  const togglePayloadExpanded = useCallback((id: number) => {
    setExpandedPayloadIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleDiffExpanded = useCallback((id: number) => {
    setExpandedDiffIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleResponseExpanded = useCallback((id: number) => {
    setExpandedResponseIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setModelOptions(c);
        if (c.openRouterModels.length > 0)
          setCloudModelId((prev) => prev || c.openRouterModel || c.openRouterModels[0]!.id);
        if (c.ollamaModels.length > 0)
          setLocalModelId((prev) => prev || c.ollamaModel || c.ollamaModels[0]!.id);
      })
      .catch(() => setModelOptions(null));
  }, []);

  useEffect(() => {
    if (!modelOptions) return;
    if (modelSource === "cloud" && modelOptions.openRouterModels.length > 0 && !modelOptions.openRouterModels.some((m) => m.id === cloudModelId))
      setCloudModelId(modelOptions.openRouterModel || modelOptions.openRouterModels[0]!.id);
    if (modelSource === "local" && modelOptions.ollamaModels.length > 0 && !modelOptions.ollamaModels.some((m) => m.id === localModelId))
      setLocalModelId(modelOptions.ollamaModel || modelOptions.ollamaModels[0]!.id);
  }, [modelOptions]);

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

  const isProjectMode = tableNames.length > 1;

  async function onGenerate() {
    setStatus(modelSource === "cloud" ? "Calling cloud LLM…" : "Calling local LLM…");
    try {
      if (isProjectMode) {
        const tablesArr = Object.values(tables);
        const requestPayload = {
          prompt,
          tables: tablesArr.map((t) => ({
            name: t.name,
            schema: t.schema,
            sampleRows: t.rows.slice(0, 10)
          })),
          modelSource,
          cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
          localModelId: modelSource === "local" ? localModelId : undefined
        };
        const nextPlan = await requestProjectPlan({
          prompt,
          tables: tablesArr,
          modelSource,
          cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
          localModelId: modelSource === "local" ? localModelId : undefined
        });
        setPlan(nextPlan);
        const preview = applyProjectPlan(tables, nextPlan);
        setDiff(preview.diff);
        setNewTablesPreview(preview.newTables);
        setConversations((prev) => {
          const nextId = (prev[0]?.id ?? 0) + 1;
          const entry: ConversationEntry = {
            id: nextId,
            prompt,
            payload: requestPayload,
            plan: nextPlan,
            diff: preview.diff,
            createdAt: new Date().toLocaleString(),
            modelSource,
            modelId: modelSource === "cloud" ? cloudModelId : localModelId
          };
          return [entry, ...prev];
        });
        setStatus("Plan generated. Review Diff, then Apply.");
      } else {
        const t = Object.values(tables)[0]!;
        const requestPayload = {
          prompt,
          schema: t.schema,
          sampleRows: t.rows.slice(0, 10),
          modelSource,
          cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
          localModelId: modelSource === "local" ? localModelId : undefined
        };
        const nextPlan = await requestPlan({
          prompt: requestPayload.prompt,
          schema: requestPayload.schema,
          sampleRows: requestPayload.sampleRows,
          modelSource: requestPayload.modelSource,
          cloudModelId: requestPayload.cloudModelId,
          localModelId: requestPayload.localModelId
        });
        setPlan(nextPlan);
        const preview = applyPlan(t.rows, t.schema, nextPlan);
        setDiff(preview.diff);
        setNewTablesPreview([]);
        setConversations((prev) => {
          const nextId = (prev[0]?.id ?? 0) + 1;
          const entry: ConversationEntry = {
            id: nextId,
            prompt,
            payload: requestPayload,
            plan: nextPlan,
            diff: preview.diff,
            createdAt: new Date().toLocaleString(),
            modelSource,
            modelId: modelSource === "cloud" ? cloudModelId : localModelId
          };
          return [entry, ...prev];
        });
        setStatus("Plan generated. Review Diff, then Apply.");
      }
    } catch (e: unknown) {
      setStatus("Error: " + String((e as Error)?.message ?? e));
    }
  }

  function onUndo() {
    if (history.length === 0) {
      setStatus("Nothing to undo.");
      return;
    }
    const last = history[history.length - 1];
    setTables(clone(last));
    setHistory((h) => h.slice(0, -1));
    setStatus("Undone last apply.");
  }

  function onApply() {
    if (!plan) return;
    setHistory((h) => [...h, clone(tables)]);

    if (isProjectMode) {
      const result = applyProjectPlan(tables, plan);
      setTables(result.tables);
      if (result.newTables.length > 0) {
        setActiveTable(result.newTables[0]);
      }
    } else {
      const t = Object.values(tables)[0]!;
      const out = applyPlan(t.rows, t.schema, plan);
      setTables({ [t.name]: { ...t, rows: out.rows, schema: out.schema } });
    }

    setStatus("Applied.");
    setPrompt("");
    setPlan(null);
    setDiff(null);
    setNewTablesPreview([]);
  }

  function onAddTable() {
    const base = "Sheet";
    let n = tableNames.length + 1;
    let name = `${base}${n}`;
    while (tables[name]) {
      n++;
      name = `${base}${n}`;
    }
    const newTable = createTable(name, [{ A: "", B: "", C: "", D: "", E: "" }]);
    setTables((prev) => ({ ...prev, [name]: newTable }));
    setActiveTable(name);
  }

  function onRemoveTable(name: string) {
    if (tableNames.length <= 1) return;
    const remaining = tableNames.filter((n) => n !== name);
    setTables((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    if (activeTable === name && remaining.length > 0) {
      setActiveTable(remaining[0]!);
    }
  }

  function onUpdateCurrentRows(rows: Record<string, any>[]) {
    if (!currentTable) return;
    const schema = inferSchema(rows);
    setTables((prev) => ({
      ...prev,
      [activeTable]: { name: activeTable, rows, schema }
    }));
  }

  function onAddRow() {
    if (!currentTable) return;
    const { schema, rows } = currentTable;
    let nextSchema = schema;
    let newRow: Record<string, any>;
    if (schema.length === 0) {
      nextSchema = [{ key: "A", type: "string" as const }];
      newRow = { A: "" };
    } else {
      newRow = nextSchema.reduce<Record<string, any>>((o, c) => ({ ...o, [c.key]: "" }), {});
    }
    const nextRows = [...rows, newRow];
    setTables((prev) => ({
      ...prev,
      [activeTable]: { name: activeTable, rows: nextRows, schema: nextSchema }
    }));
  }

  function onAddColumn() {
    if (!currentTable) return;
    const { schema, rows } = currentTable;
    const newKey = indexToCol(schema.length);
    const nextSchema = [...schema, { key: newKey, type: "string" as const }];
    const nextRows =
      rows.length === 0
        ? [{ [newKey]: "" }]
        : rows.map((r) => ({ ...r, [newKey]: "" }));
    setTables((prev) => ({
      ...prev,
      [activeTable]: { name: activeTable, rows: nextRows, schema: nextSchema }
    }));
  }

  function applyFormatToSelection(updater: (prev: CellFormat) => CellFormat) {
    const api = gridRef.current;
    if (!api || !currentTable) return;
    const nodes = api.getSelectedNodes();
    if (nodes.length === 0) return;
    const colIds = currentTable.schema.map((s) => s.key);
    setCellFormats((prev) => {
      const next = { ...prev };
      for (const node of nodes) {
        const rowIndex = node.rowIndex;
        if (rowIndex == null) continue;
        for (const colId of colIds) {
          const key = `${activeTable}:${rowIndex}:${colId}`;
          next[key] = updater(prev[key] ?? {});
        }
      }
      return next;
    });
    setTimeout(refreshCells, 0);
  }

  function onToolbarBold() {
    applyFormatToSelection((f) => ({ ...f, bold: !f.bold }));
  }
  function onToolbarItalic() {
    applyFormatToSelection((f) => ({ ...f, italic: !f.italic }));
  }
  function onToolbarUnderline() {
    applyFormatToSelection((f) => ({ ...f, underline: !f.underline }));
  }
  function onToolbarAlign(align: "left" | "center" | "right") {
    applyFormatToSelection((f) => ({ ...f, textAlign: align }));
  }
  function onToolbarFont(e: React.ChangeEvent<HTMLSelectElement>) {
    const v = e.target.value;
    setToolbarFont(v);
    applyFormatToSelection((f) => ({ ...f, fontFamily: v }));
  }
  function onToolbarFontSize(e: React.ChangeEvent<HTMLSelectElement>) {
    const v = parseInt(e.target.value, 10);
    setToolbarFontSize(v);
    applyFormatToSelection((f) => ({ ...f, fontSize: v }));
  }
  function onToolbarBgColor() {
    applyFormatToSelection((f) => ({
      ...f,
      backgroundColor: f.backgroundColor ? undefined : "#ffffcc"
    }));
  }

  async function onDownloadExcel() {
    setStatus("正在导出…");
    try {
      const tablesArr = Object.values(tables);
      const blob = await exportProjectToExcel(tablesArr);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "project.xlsx";
      a.click();
      URL.revokeObjectURL(url);
      setStatus("已下载 project.xlsx");
    } catch (e: unknown) {
      setStatus("导出失败: " + String((e as Error)?.message ?? e));
    }
  }

  const placeholder = isProjectMode
    ? 'e.g. "Join Sheet1 and Orders on name and customer" or "Add column total to Sheet1"'
    : 'Try: "Add a column total_price = price * quantity"';

  const renderJsonPreview = (
    value: unknown,
    maxLines: number,
    expanded: boolean,
    onToggle: () => void
  ) => {
    if (value == null) return null;
    const full = JSON.stringify(value, null, 2);
    const lines = full.split("\n");
    if (expanded || lines.length <= maxLines) {
      return (
        <>
          <pre>{full}</pre>
          {lines.length > maxLines && (
            <button
              type="button"
              className="btn conversation-btn"
              onClick={onToggle}
            >
              收起 Diff
            </button>
          )}
        </>
      );
    }
    const limited = lines.slice(0, maxLines);
    const remaining = lines.length - maxLines;
    return (
      <>
        <pre>{limited.join("\n") + `\n…(还有 ${remaining} 行)`}</pre>
        <button
          type="button"
          className="btn conversation-btn"
          onClick={onToggle}
        >
          展开全部 Diff
        </button>
      </>
    );
  };

  return (
    <>
      <div className="header">
        <div style={{ fontWeight: 600 }}>Cursor for Spreadsheet — 多表项目</div>
        <div className="small">
          <span className="kbd">Cmd</span>+<span className="kbd">K</span> 聚焦 AI 面板
        </div>
        <div style={{ marginLeft: "auto" }} className="small">
          {status}
        </div>
      </div>

      <div className="toolbar">
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn" title="撤销" onClick={onUndo} disabled={history.length === 0}>
            ↩
          </button>
          <button type="button" className="toolbar-btn" title="重做" disabled>
            ↪
          </button>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <select
            className="toolbar-select"
            value={toolbarFont}
            onChange={onToolbarFont}
            title="字体"
          >
            <option value="system-ui">系统默认</option>
            <option value="Arial">Arial</option>
            <option value="Georgia">Georgia</option>
            <option value="monospace">等宽</option>
          </select>
          <select
            className="toolbar-select toolbar-select-narrow"
            value={toolbarFontSize}
            onChange={onToolbarFontSize}
            title="字号"
          >
            <option value="10">10</option>
            <option value="11">11</option>
            <option value="12">12</option>
            <option value="14">14</option>
            <option value="16">16</option>
          </select>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn" title="粗体" onClick={onToolbarBold}>
            <strong>B</strong>
          </button>
          <button type="button" className="toolbar-btn" title="斜体" onClick={onToolbarItalic}>
            <em>I</em>
          </button>
          <button type="button" className="toolbar-btn" title="下划线" onClick={onToolbarUnderline}>
            U
          </button>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="左对齐" onClick={() => onToolbarAlign("left")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="4" y1="12" x2="16" y2="12" />
              <line x1="4" y1="18" x2="14" y2="18" />
            </svg>
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="居中" onClick={() => onToolbarAlign("center")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="8" y1="12" x2="16" y2="12" />
              <line x1="6" y1="18" x2="18" y2="18" />
            </svg>
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="右对齐" onClick={() => onToolbarAlign("right")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="8" y1="12" x2="20" y2="12" />
              <line x1="10" y1="18" x2="20" y2="18" />
            </svg>
          </button>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn" title="填充颜色" onClick={onToolbarBgColor}>
            ▤
          </button>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="添加行" onClick={onAddRow}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="4" y1="8" x2="14" y2="8" />
              <line x1="4" y1="12" x2="18" y2="12" />
              <line x1="4" y1="16" x2="12" y2="16" />
              <line x1="20" y1="10" x2="20" y2="14" />
              <line x1="18" y1="12" x2="22" y2="12" />
            </svg>
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="添加列" onClick={onAddColumn}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="8" y1="4" x2="8" y2="14" />
              <line x1="12" y1="4" x2="12" y2="18" />
              <line x1="16" y1="4" x2="16" y2="12" />
              <line x1="20" y1="18" x2="20" y2="22" />
              <line x1="18" y1="20" x2="22" y2="20" />
            </svg>
          </button>
        </div>
        <div className="toolbar-group" style={{ marginLeft: "auto" }}>
          <button type="button" className="toolbar-btn" title="下载为 Excel" onClick={onDownloadExcel}>
            ⬇
          </button>
        </div>
      </div>

      <div className="tabs-row">
        {tableNames.map((name) => (
          <div key={name} className={`tab ${activeTable === name ? "active" : ""}`}>
            <button
              type="button"
              className="tab-btn"
              title={`切换到 ${name}`}
              onClick={() => setActiveTable(name)}
            >
              {name}
            </button>
            {tableNames.length > 1 && (
              <button
                type="button"
                className="tab-close"
                onClick={() => onRemoveTable(name)}
                title="删除表"
              >
                ×
              </button>
            )}
          </div>
        ))}
        <button type="button" className="btn tab-add" title="添加新表" onClick={onAddTable}>
          + 新表
        </button>
      </div>

      <div className="container">
        <div className="grid ag-theme-quartz">
          {currentTable && (
            <AgGridReact
              key={activeTable}
              rowData={currentTable.rows}
              columnDefs={colDefs}
              defaultColDef={{ resizable: true, sortable: true, filter: true }}
              rowSelection="multiple"
              onGridReady={(e) => {
                gridRef.current = e.api;
              }}
              onCellValueChanged={(e) => {
                const idx = e.rowIndex!;
                const next = [...currentTable.rows];
                next[idx] = { ...next[idx], [e.colDef.field!]: e.newValue };
                onUpdateCurrentRows(next);
              }}
            />
          )}
        </div>

        <div className={`side-panel ${aiPanelCollapsed ? "collapsed" : ""}`}>
          <div className="panel-content">
          <div className="panel-section ai-panel">
            <div style={{ fontWeight: 600, marginBottom: 8 }}>AI Edit</div>
            <div className="ai-tabs">
              <button
                type="button"
                className={`ai-tab ${activeAiTab === "chat" ? "active" : ""}`}
                onClick={() => setActiveAiTab("chat")}
              >
                Chat
              </button>
              <button
                type="button"
                className={`ai-tab ${activeAiTab === "history" ? "active" : ""}`}
                onClick={() => setActiveAiTab("history")}
              >
                历史记录
              </button>
            </div>

            {activeAiTab === "chat" && (
              <>
                {isProjectMode && (
                  <div className="small" style={{ color: "#0066cc" }}>
                    项目模式：可对多张表进行 join / create_table 等操作
                  </div>
                )}
                <div className="model-switch">
                  <div className="model-source-row">
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
                      本地
                    </label>
                  </div>
                  {modelSource === "cloud" && modelOptions?.openRouterModels && (
                    <select
                      className="model-select"
                      value={cloudModelId}
                      onChange={(e) => setCloudModelId(e.target.value)}
                      title="云端模型"
                    >
                      {modelOptions.openRouterModels.map((m: ModelOption) => (
                        <option key={m.id} value={m.id}>{m.label}</option>
                      ))}
                    </select>
                  )}
                  {modelSource === "local" && modelOptions?.ollamaModels && (
                    <select
                      className="model-select"
                      value={localModelId}
                      onChange={(e) => setLocalModelId(e.target.value)}
                      title="本地模型"
                    >
                      {modelOptions.ollamaModels.map((m: ModelOption) => (
                        <option key={m.id} value={m.id}>{m.label}</option>
                      ))}
                    </select>
                  )}
                </div>
                <textarea
                  ref={promptRef}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={placeholder}
                />

                <div className="row">
                  <button className="btn primary" onClick={onGenerate}>
                    Generate Plan
                  </button>
                </div>
                {(diff || newTablesPreview.length > 0) && (
                  <>
                    <div style={{ fontWeight: 600 }}>Diff Preview</div>
                    {diff &&
                      renderJsonPreview(diff, 5, diffExpanded, () =>
                        setDiffExpanded((v) => !v)
                      )}
                    {newTablesPreview.length > 0 && (
                      <div className="small">
                        将新建表: {newTablesPreview.join(", ")}
                      </div>
                    )}
                    <div className="row">
                      <button className="btn primary" onClick={onApply}>
                        Apply
                      </button>
                      <div className="small">Apply plan to project data.</div>
                    </div>
                  </>
                )}
              </>
            )}

            {activeAiTab === "history" && conversations.length > 0 && (
              <div className="conversation-section">
                <div style={{ fontWeight: 600 }}>历史对话</div>
                <div className="conversation-list">
                  {conversations.map((item) => (
                    <div key={item.id} className="conversation-item">
                      <div className="conversation-header">
                        <div className="small">
                          #{item.id} · {item.modelSource === "cloud" ? "云端" : "本地"}{" "}
                          {item.modelId || ""}
                        </div>
                        <div className="small">{item.createdAt}</div>
                      </div>
                      <div className="conversation-prompt">
                        <span className="small">Prompt：</span>{" "}
                        {item.prompt ? item.prompt : "(空)"}
                      </div>
                      <div className="conversation-actions">
                        <button
                          type="button"
                          className="btn conversation-btn"
                          onClick={() => togglePayloadExpanded(item.id)}
                        >
                          {expandedPayloadIds.has(item.id)
                            ? "隐藏发送给 AI 的内容"
                            : "查看发送给 AI 的内容"}
                        </button>
                        <button
                          type="button"
                          className="btn conversation-btn"
                          onClick={() => toggleResponseExpanded(item.id)}
                        >
                          {expandedResponseIds.has(item.id)
                            ? "隐藏 AI 回复"
                            : "查看 AI 回复"}
                        </button>
                      </div>
                      {expandedPayloadIds.has(item.id) && (
                        <pre>{JSON.stringify(item.payload, null, 2)}</pre>
                      )}
                      {expandedResponseIds.has(item.id) && item.plan && (
                        <>
                          <div style={{ fontWeight: 600, marginTop: 4 }}>Plan</div>
                          <pre>{JSON.stringify(item.plan, null, 2)}</pre>
                          {item.diff && (
                            <>
                              <div style={{ fontWeight: 600, marginTop: 4 }}>Diff</div>
                              {renderJsonPreview(
                                item.diff,
                                5,
                                expandedDiffIds.has(item.id),
                                () => toggleDiffExpanded(item.id)
                              )}
                            </>
                          )}
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="panel-section schema-section">
            <button
              type="button"
              className="schema-toggle"
              onClick={() => setSchemaExpanded((v) => !v)}
            >
              <span className="schema-toggle-icon">
                {schemaExpanded ? "▼" : "▶"}
              </span>
              Schema {isProjectMode && `(${activeTable})`}
            </button>
            {schemaExpanded && currentTable && (
              <>
                <pre>{JSON.stringify(currentTable.schema, null, 2)}</pre>
                <div className="small">
                  {isProjectMode
                    ? "项目内所有表的 schema + sample rows 会发送给 LLM。"
                    : "This schema + sample rows are what the LLM sees."}
                </div>
              </>
            )}
          </div>
          </div>
          <button
            type="button"
            className="panel-toggle"
            onClick={() => setAiPanelCollapsed((v) => !v)}
            title={aiPanelCollapsed ? "展开 AI 面板" : "折叠 AI 面板"}
          >
            {aiPanelCollapsed ? "◀" : "▶"}
          </button>
        </div>
      </div>
    </>
  );
}
