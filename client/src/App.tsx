import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import type { GridApi } from "ag-grid-community";

import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";

import type { CellFormat, Diff, Plan, PreviewRecord, SchemaCol, TableData } from "./types";
import { applyProjectPlan, inferSchema } from "./engine";
import { requestAgentProjectPlanViaStream } from "./agentProjectPlan";
import {
  exportProjectToExcel,
  executePlanOnServer,
  executeProjectPlanById,
  fetchConfig,
  fetchSampleTables,
  fetchSampleTablesWithRetry,
  requestAgentProjectPlan,
  splitApiErrorDetail,
  uploadProjectFile
} from "./llm";
import type { ChatMessage, ConfigResponse, ModelOption } from "./llm";
import { ClarificationBubble } from "./ClarificationBubble";
import { buildAgentRequestContext } from "./agentContext";
import {
  buildClarificationTechnicalHistoryEntry,
  type AgentClarificationHistoryPayload
} from "./agentStream";
import {
  truncatePromptAnchor,
  type PendingClarification,
  type PendingClarificationSource
} from "./clarification";
import { generateTraceId, getSessionId, logError, logInfo } from "./logger";
import {
  debouncedSyncWorkspaceMemoryToServer,
  flushDebouncedSessionMemorySync,
  hydrateWorkspaceMemoryFromServer,
  setSessionSyncSuccessHandler
} from "./sessionMemorySync";
import {
  debouncedSaveWorkspaceMemory,
  flushDebouncedWorkspaceMemorySave,
  appendApplyLogEntry,
  appendClarificationAnswerToTranscript,
  appendClarificationQuestionToTranscript,
  buildAgentHistoryForRequest,
  createApplyLogEntry,
  emptyWorkspaceMemory,
  formatLastApplyHint,
  loadWorkspaceMemory,
  saveWorkspaceMemory,
  syncAgentTranscriptFromChat,
  updateSessionBootId,
  type AppliedPlanEntry,
  type WorkspaceMemory
} from "./workspaceMemory";
import {
  loadModelPreference,
  resolveModelPreference,
  saveModelPreference,
} from "./modelPreferenceStorage";
import type { CustomModels } from "./customModelStorage";
import {
  addCustomModel,
  loadCustomModels,
  mergeCustomModels,
  normalizeCustomModelInput,
  removeCustomModel,
  saveCustomModels,
} from "./customModelStorage";
import {
  BUILTIN_SAMPLE_WORKSPACE_KEY,
  debouncedSaveWorkspaceHistory,
  flushDebouncedWorkspaceHistorySave,
  formatModelTag,
  hashFileToWorkspaceKey,
  loadWorkspaceHistory,
  workspaceHistoryHasContent
} from "./workspaceHistoryStorage";
import { loadWorkspaceRules, saveWorkspaceRules } from "./workspaceRulesStorage";

const initialModelPreference = loadModelPreference();

/** Single-model product phase — aligned with server ``OPENROUTER_MODEL`` default. */
const PRODUCT_CLOUD_MODEL_ID = "deepseek/deepseek-v4-pro";
/** Model switch UI hidden until multi-model is an explicit product decision. */
const SHOW_MODEL_SWITCH = false;

type ConversationEntry = {
  id: number;
  prompt: string;
  payload: any;
  plan: Plan | null;
  diff: Diff | null;
  createdAt: string;
  modelSource: "cloud" | "local";
  modelId: string | null;
  modelTag?: string;
  mode?: "agent_clarification";
  clarification?: AgentClarificationHistoryPayload;
};

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

function statusFromErrorMessage(message: string): string {
  if (message.includes("云端 LLM 鉴权失败")) {
    return (
      "云端 LLM 鉴权失败：请检查 OPENROUTER_API_KEY 并重启后端，" +
      "或在 OpenRouter 控制台确认 Key 有效。完整原因已写入浏览器控制台日志。"
    );
  }
  return "Error: " + message;
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
  onRenameColumn: (oldKey: string, newKey: string) => void,
  diff: Diff | null,
  gridEditable: boolean
): ColDef[] {
  const dataCols: ColDef[] = schema.map((c) => {
    const isAdded = !!diff && diff.addedColumns.includes(c.key);
    const isModified = !!diff && diff.modifiedColumns.includes(c.key);

    const headerClass =
      [
        isAdded ? "col-header-added" : "",
        isModified ? "col-header-modified" : ""
      ]
        .filter(Boolean)
        .join(" ") || undefined;

    return {
      field: c.key,
      headerName: c.key,
      editable: gridEditable,
      flex: 1,
      minWidth: 140,
      headerComponent: gridEditable ? EditableHeader : undefined,
      headerComponentParams: gridEditable
        ? {
            displayName: c.key,
            onSave: (newKey: string) => onRenameColumn(c.key, newKey)
          }
        : undefined,
      headerClass,
      cellClass: () => {
        const classes: string[] = [];
        if (isAdded) classes.push("cell-added");
        if (isModified) classes.push("cell-modified");
        return classes;
      },
      cellStyle: (params) => {
        const rowIndex = params.rowIndex ?? params.node?.rowIndex ?? undefined;
        const colId = params.colDef?.field ?? c.key;
        if (rowIndex == null) return undefined;
        const key = `${activeTable}:${rowIndex}:${colId}`;
        const fmt = cellFormats[key];
        return fmt ? (formatToCellStyle(fmt) as Record<string, string | number>) : undefined;
      }
    };
  });
  return [ROW_NUM_COL, ...dataCols];
}

function previewReadyStatusMessage(warnings?: string[], summary?: string | null): string {
  const base =
    "服务器预览已就绪：Apply 写回、Abort 放弃预览，或输入修订说明后点 Revise。";
  let msg = warnings?.length ? `${base} （已达修订上限：${warnings.join(" ")}）` : base;
  if (summary) {
    // p1-preview-summary-wire：摘要须搭配免责声明——异步生成，可能有误。
    msg += ` AI 摘要：${summary}（AI 生成，可能有误，请以 Diff 为准）`;
  }
  return msg;
}

export default function App() {
  const clone = <T,>(v: T): T => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sc = (globalThis as any).structuredClone;
    if (typeof sc === "function") return sc(v);
    return JSON.parse(JSON.stringify(v));
  };

  const [tables, setTables] = useState<Record<string, TableData>>(() => ({
    // 初始只提供一个空白表，所有示例数据均从 test-data/sample.xlsx 加载。
    Sheet1: createTable("Sheet1", [])
  }));
  const [activeTable, setActiveTable] = useState<string>("Sheet1");
  const [history, setHistory] = useState<Array<Record<string, TableData>>>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [newTablesPreview, setNewTablesPreview] = useState<string[]>([]);
  const [agentPreviewHistory, setAgentPreviewHistory] = useState<PreviewRecord[]>([]);
  const [pendingServerPreviewId, setPendingServerPreviewId] = useState<string | null>(null);
  const [agentRevisionCount, setAgentRevisionCount] = useState(0);
  const [appliedPlansSummary, setAppliedPlansSummary] = useState("");
  const [prompt, setPrompt] = useState("");
  const [modelSource, setModelSource] = useState<"cloud" | "local">(
    initialModelPreference?.modelSource ?? "cloud"
  );
  const [serverConfig, setServerConfig] = useState<ConfigResponse | null>(null);
  const [customModels, setCustomModels] = useState<CustomModels>(() => loadCustomModels());
  const [customModelId, setCustomModelId] = useState("");
  const [customModelLabel, setCustomModelLabel] = useState("");
  const [customModelError, setCustomModelError] = useState("");
  const modelOptions = useMemo(
    () => (serverConfig ? mergeCustomModels(serverConfig, customModels) : null),
    [serverConfig, customModels]
  );
  const [cloudModelId, setCloudModelId] = useState<string>(
    initialModelPreference?.cloudModelId ?? ""
  );
  const [localModelId, setLocalModelId] = useState<string>(
    initialModelPreference?.localModelId ?? ""
  );
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
  const [activeAiTab, setActiveAiTab] = useState<"chat" | "history" | "schema">(
    "chat"
  );
  const [projectId, setProjectId] = useState<string | null>(null);
  const [loadSampleLoading, setLoadSampleLoading] = useState(true);
  const [loadSampleError, setLoadSampleError] = useState<string | null>(null);
  const gridRef = useRef<GridApi | null>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(
    null
  );
  const [applyLog, setApplyLog] = useState<AppliedPlanEntry[]>([]);
  const [workspaceSessionId, setWorkspaceSessionId] = useState<string | null>(null);
  const [showBackendRestartBanner, setShowBackendRestartBanner] = useState(false);
  const [gridSelectionSummary, setGridSelectionSummary] = useState<string | null>(null);
  const [workspaceRules, setWorkspaceRules] = useState("");
  const [activeWorkspaceKey, setActiveWorkspaceKey] = useState<string | null>(null);
  const [serverBootId, setServerBootId] = useState<string | null>(null);
  const [sessionMemoryEnabled, setSessionMemoryEnabled] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const skipMemorySaveRef = useRef(false);
  const workspaceMemoryRef = useRef<WorkspaceMemory>(emptyWorkspaceMemory());
  const sessionMemoryEnabledRef = useRef(false);
  const projectIdRef = useRef<string | null>(null);
  const serverHydrateAttemptedRef = useRef<string | null>(null);
  const skipWorkspaceSaveRef = useRef(false);
  const diffPreviewLoggedRef = useRef(false);
  const lastAgentPlanPromptRef = useRef("");
  const agentLlmAbortRef = useRef<AbortController | null>(null);

  const takeAgentLlmAbortSignal = () => {
    agentLlmAbortRef.current?.abort();
    const c = new AbortController();
    agentLlmAbortRef.current = c;
    return c.signal;
  };

  useEffect(() => {
    logInfo("app_open", {
      sessionId: getSessionId(),
      userAgent: typeof navigator !== "undefined" ? navigator.userAgent : "",
      language: typeof navigator !== "undefined" ? navigator.language : ""
    });
  }, []);

  const resolveModelLabel = useCallback(
    (source: "cloud" | "local", modelId: string | null): string | undefined => {
      if (!modelId || !modelOptions) return undefined;
      const list =
        source === "cloud" ? modelOptions.openRouterModels : modelOptions.ollamaModels;
      return list.find((m) => m.id === modelId)?.label;
    },
    [modelOptions]
  );

  const buildModelTag = useCallback(
    (source: "cloud" | "local", modelId: string | null): string =>
      formatModelTag(source, modelId, resolveModelLabel(source, modelId)),
    [resolveModelLabel]
  );

  const applyHydratedMemory = useCallback(
    (workspaceKey: string, memory: WorkspaceMemory, bootId: string | null) => {
      const cached = loadWorkspaceHistory(workspaceKey);
      skipMemorySaveRef.current = true;
      skipWorkspaceSaveRef.current = true;
      setConversations((cached?.conversations ?? []) as ConversationEntry[]);
      setChatMessages(memory.chatTranscript);
      setAppliedPlansSummary(memory.appliedPlansSummary);
      setAgentPreviewHistory(memory.previewHistory);
      setApplyLog(memory.applyLog);
      setWorkspaceSessionId(memory.sessionMeta.sessionId);
      setShowBackendRestartBanner(
        !!(
          bootId &&
          memory.sessionMeta.lastServerBootId &&
          memory.sessionMeta.lastServerBootId !== bootId &&
          memory.chatTranscript.length > 0
        )
      );
      workspaceMemoryRef.current = bootId ? updateSessionBootId(memory, bootId) : memory;
    },
    []
  );

  const hydrateWorkspaceMemory = useCallback(
    async (workspaceKey: string, bootId: string | null) => {
      let memory = loadWorkspaceMemory(workspaceKey, bootId);
      if (sessionMemoryEnabledRef.current && memory.sessionMeta.sessionId) {
        try {
          memory = await hydrateWorkspaceMemoryFromServer(memory);
          saveWorkspaceMemory(workspaceKey, memory);
          serverHydrateAttemptedRef.current = workspaceKey;
        } catch (e) {
          logError("session_memory_hydrate", {
            message: (e as Error)?.message ?? String(e)
          });
        }
      }
      applyHydratedMemory(workspaceKey, memory, bootId);
    },
    [applyHydratedMemory]
  );

  const activateWorkspace = useCallback(
    (workspaceKey: string) => {
      flushDebouncedWorkspaceMemorySave();
      flushDebouncedWorkspaceHistorySave();
      void flushDebouncedSessionMemorySync();
      serverHydrateAttemptedRef.current = null;
      setActiveWorkspaceKey(workspaceKey);
      void hydrateWorkspaceMemory(workspaceKey, serverBootId);
      setWorkspaceRules(loadWorkspaceRules(workspaceKey));
    },
    [hydrateWorkspaceMemory, serverBootId]
  );

  const loadSampleTables = useCallback(async () => {
    logInfo("sample_load_manual", { sessionId: getSessionId() });
    setLoadSampleLoading(true);
    setLoadSampleError(null);
    try {
      const res = await fetchSampleTables();
      const loaded = res.tables;
      if (!loaded || loaded.length === 0) return;
      setTables(() => {
        const next: Record<string, TableData> = {};
        for (const t of loaded) {
          next[t.name] = { name: t.name, rows: t.rows, schema: t.schema };
        }
        return next;
      });
      setProjectId(res.projectId);
      setActiveTable(loaded[0].name);
      activateWorkspace(BUILTIN_SAMPLE_WORKSPACE_KEY);
      setStatus("已从 test-data/sample.xlsx 加载示例数据");
      logInfo("sample_load_manual_success", {
        projectId: res.projectId,
        tableCount: loaded.length
      });
    } catch (e) {
      const msg = (e as Error)?.message ?? String(e);
      setLoadSampleError(msg);
      setStatus("加载示例失败: " + msg);
      logError("sample_load_manual_error", { message: msg });
    } finally {
      setLoadSampleLoading(false);
    }
  }, [activateWorkspace]);

  const tableNames = Object.keys(tables);
  const currentTable = tables[activeTable];

  const planPreview = useMemo(() => {
    if (!plan) return null;
    return applyProjectPlan(tables, plan);
  }, [plan, tables]);

  const displayTables = planPreview?.tables ?? tables;
  const displayTableNames = Object.keys(displayTables);
  const isPreviewMode = plan != null;
  const currentDisplayTable = displayTables[activeTable];

  useEffect(() => {
    if (!plan || !planPreview) return;
    if (displayTables[activeTable]) return;
    const fallback =
      planPreview.newTables.find((n) => displayTables[n]) ?? displayTableNames[0];
    if (fallback) setActiveTable(fallback);
  }, [plan, planPreview, activeTable, displayTables, displayTableNames]);

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
      currentDisplayTable
        ? schemaToColDefs(
            currentDisplayTable.schema,
            activeTable,
            cellFormats,
            onRenameColumn,
            diff,
            !isPreviewMode
          )
        : [],
    [currentDisplayTable, activeTable, cellFormats, onRenameColumn, diff, isPreviewMode]
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

  const onImportClick = useCallback(() => {
    logInfo("import_file_click", {});
    fileInputRef.current?.click();
  }, []);

  const onFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) {
        // 允许用户取消选择；清空以便后续选择同一文件仍能触发 change。
        e.target.value = "";
        return;
      }
      setImportLoading(true);
      setImportError(null);
      setStatus("正在导入文件…（若超过 20 秒仍未完成，请检查文件大小或后端日志）");
      try {
        const workspaceKey = await hashFileToWorkspaceKey(file);
        const res = await uploadProjectFile(file);
        const loaded = res.tables;
        if (!loaded || loaded.length === 0) {
          setStatus("导入成功，但未识别到任何表数据");
        } else {
          setTables(() => {
            const next: Record<string, TableData> = {};
            for (const t of loaded) {
              next[t.name] = { name: t.name, rows: t.rows, schema: t.schema };
            }
            return next;
          });
          setProjectId(res.projectId);
          setActiveTable(loaded[0].name);
          // 清空旧项目相关状态，避免在新项目上复用历史 Plan / Diff。
          setHistory([]);
          setPlan(null);
          setDiff(null);
          setNewTablesPreview([]);
          setCellFormats({});
          activateWorkspace(workspaceKey);
          setStatus(`已从上传文件导入 ${loaded.length} 张表`);
          logInfo("import_file_success", {
            projectId: res.projectId,
            tableCount: loaded.length
          });
        }
      } catch (err) {
        const msg = (err as Error)?.message ?? String(err);
        setImportError(msg);
        setStatus("导入失败: " + msg);
        logError("import_file_error", { message: msg });
      } finally {
        setImportLoading(false);
        // 允许选择同一个文件时仍能触发 change。
        e.target.value = "";
      }
    },
    [activateWorkspace]
  );

  // 启动时从后端 test-data/sample.xlsx 加载示例表格，带重试以应对后端尚未就绪。
  useEffect(() => {
    (async () => {
      setLoadSampleLoading(true);
      setLoadSampleError(null);
      try {
        const res = await fetchSampleTablesWithRetry({
          maxRetries: 3,
          delayMs: 1500,
          timeoutMs: 8000
        });
        const loaded = res.tables;
        if (!loaded || loaded.length === 0) return;
        setTables(() => {
          const next: Record<string, TableData> = {};
          for (const t of loaded) {
            next[t.name] = { name: t.name, rows: t.rows, schema: t.schema };
          }
          return next;
        });
        setProjectId(res.projectId);
        setActiveTable(loaded[0].name);
        activateWorkspace(BUILTIN_SAMPLE_WORKSPACE_KEY);
        setStatus("已从 test-data/sample.xlsx 加载示例数据");
        logInfo("sample_load_auto", {
          success: true,
          projectId: res.projectId,
          tableCount: loaded.length,
          maxRetries: 3
        });
      } catch (e) {
        const msg = (e as Error)?.message ?? String(e);
        setLoadSampleError(msg);
        setStatus("加载示例失败，请检查后端是否在 http://localhost:8787 运行");
        logError("sample_load_auto", {
          success: false,
          message: msg,
          maxRetries: 3
        });
      } finally {
        setLoadSampleLoading(false);
      }
    })();
  }, [activateWorkspace]);

  useEffect(() => {
    if (!serverBootId || !activeWorkspaceKey) {
      return;
    }
    const memory = workspaceMemoryRef.current;
    const prevBoot = memory.sessionMeta.lastServerBootId;
    if (prevBoot && prevBoot !== serverBootId && memory.chatTranscript.length > 0) {
      setShowBackendRestartBanner(true);
    }
    workspaceMemoryRef.current = updateSessionBootId(memory, serverBootId);
  }, [serverBootId, activeWorkspaceKey]);

  useEffect(() => {
    sessionMemoryEnabledRef.current = sessionMemoryEnabled;
  }, [sessionMemoryEnabled]);

  useEffect(() => {
    if (!sessionMemoryEnabled || !activeWorkspaceKey) {
      return;
    }
    if (serverHydrateAttemptedRef.current === activeWorkspaceKey) {
      return;
    }
    void (async () => {
      const memory = loadWorkspaceMemory(activeWorkspaceKey, serverBootId);
      if (!memory.sessionMeta.sessionId) {
        return;
      }
      try {
        const merged = await hydrateWorkspaceMemoryFromServer(memory);
        serverHydrateAttemptedRef.current = activeWorkspaceKey;
        saveWorkspaceMemory(activeWorkspaceKey, merged);
        applyHydratedMemory(activeWorkspaceKey, merged, serverBootId);
      } catch (e) {
        logError("session_memory_hydrate", {
          message: (e as Error)?.message ?? String(e)
        });
      }
    })();
  }, [sessionMemoryEnabled, activeWorkspaceKey, serverBootId, applyHydratedMemory]);

  useEffect(() => {
    setSessionSyncSuccessHandler((sessionId, memory) => {
      if (workspaceMemoryRef.current.sessionMeta.sessionId !== sessionId) {
        return;
      }
      workspaceMemoryRef.current = memory;
      if (activeWorkspaceKey) {
        saveWorkspaceMemory(activeWorkspaceKey, memory);
      }
    });
    return () => setSessionSyncSuccessHandler(null);
  }, [activeWorkspaceKey]);

  useEffect(() => {
    projectIdRef.current = projectId;
  }, [projectId]);

  const scheduleSessionMemorySync = useCallback(
    (memory: WorkspaceMemory, workspaceKey: string | null) => {
      if (!sessionMemoryEnabledRef.current || !workspaceKey) {
        return;
      }
      const sessionId = memory.sessionMeta.sessionId;
      if (!sessionId) {
        return;
      }
      debouncedSyncWorkspaceMemoryToServer(sessionId, memory, {
        projectId: projectIdRef.current,
        workspaceKey
      });
    },
    []
  );

  useEffect(() => {
    if (!activeWorkspaceKey) {
      return;
    }
    if (skipMemorySaveRef.current) {
      skipMemorySaveRef.current = false;
      return;
    }
    const updated = syncAgentTranscriptFromChat(
      {
        ...workspaceMemoryRef.current,
        appliedPlansSummary,
        previewHistory: agentPreviewHistory,
        applyLog
      },
      chatMessages
    );
    updated.sessionMeta = {
      ...updated.sessionMeta,
      lastServerBootId: serverBootId ?? updated.sessionMeta.lastServerBootId
    };
    workspaceMemoryRef.current = updated;
    debouncedSaveWorkspaceMemory(activeWorkspaceKey, updated);
    scheduleSessionMemorySync(updated, activeWorkspaceKey);
    return () => {
      flushDebouncedWorkspaceMemorySave();
      void flushDebouncedSessionMemorySync();
    };
  }, [
    activeWorkspaceKey,
    chatMessages,
    appliedPlansSummary,
    agentPreviewHistory,
    applyLog,
    serverBootId,
    scheduleSessionMemorySync
  ]);

  useEffect(() => {
    if (!activeWorkspaceKey) {
      return;
    }
    if (skipWorkspaceSaveRef.current) {
      skipWorkspaceSaveRef.current = false;
      return;
    }
    debouncedSaveWorkspaceHistory(activeWorkspaceKey, { conversations });
    return () => {
      flushDebouncedWorkspaceHistorySave();
    };
  }, [activeWorkspaceKey, conversations]);

  useEffect(() => {
    if (!activeWorkspaceKey) {
      return;
    }
    saveWorkspaceRules(activeWorkspaceKey, workspaceRules);
  }, [activeWorkspaceKey, workspaceRules]);

  const buildAgentContextPayload = useCallback(
    () => buildAgentRequestContext(activeTable, gridRef.current, workspaceRules),
    [activeTable, workspaceRules]
  );

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
        if (c.serverBootId) {
          setServerBootId(c.serverBootId);
        }
        setSessionMemoryEnabled(!!c.sessionMemoryEnabled);
        setServerConfig(c);
        const resolved = resolveModelPreference(
          loadModelPreference(),
          mergeCustomModels(c, loadCustomModels())
        );
        const productModelId = c.openRouterModel || PRODUCT_CLOUD_MODEL_ID;
        setModelSource("cloud");
        setCloudModelId(productModelId);
        setLocalModelId(resolved.localModelId || c.ollamaModel || "");
      })
      .catch(() => setServerConfig(null));
  }, []);

  useEffect(() => {
    if (activeAiTab !== "chat") {
      return;
    }
    const el = chatScrollRef.current;
    if (!el) {
      return;
    }
    el.scrollTop = el.scrollHeight;
  }, [chatMessages, activeAiTab]);

  useEffect(() => {
    if (!modelOptions) return;
    if (modelSource === "cloud" && modelOptions.openRouterModels.length > 0 && !modelOptions.openRouterModels.some((m) => m.id === cloudModelId))
      setCloudModelId(modelOptions.openRouterModel || modelOptions.openRouterModels[0]!.id);
    if (modelSource === "local" && modelOptions.ollamaModels.length > 0 && !modelOptions.ollamaModels.some((m) => m.id === localModelId))
      setLocalModelId(modelOptions.ollamaModel || modelOptions.ollamaModels[0]!.id);
  }, [modelOptions]);

  useEffect(() => {
    if (!modelOptions || !cloudModelId || !localModelId) return;
    saveModelPreference({ modelSource, cloudModelId, localModelId });
  }, [modelOptions, modelSource, cloudModelId, localModelId]);

  const handleAddCustomModel = useCallback(() => {
    const option = normalizeCustomModelInput(customModelId, customModelLabel);
    if (!option) {
      setCustomModelError("模型 ID 不能为空，且不能包含空格");
      return;
    }
    setCustomModels((prev) => {
      const next = addCustomModel(prev, modelSource, option);
      saveCustomModels(next);
      return next;
    });
    if (modelSource === "cloud") setCloudModelId(option.id);
    else setLocalModelId(option.id);
    setCustomModelId("");
    setCustomModelLabel("");
    setCustomModelError("");
    logInfo("custom_model_add", { source: modelSource, modelId: option.id });
  }, [customModelId, customModelLabel, modelSource]);

  const handleRemoveCustomModel = useCallback(
    (id: string) => {
      setCustomModels((prev) => {
        const next = removeCustomModel(prev, modelSource, id);
        saveCustomModels(next);
        return next;
      });
      logInfo("custom_model_remove", { source: modelSource, modelId: id });
    },
    [modelSource]
  );

  const isProjectMode = tableNames.length > 1;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) {
        e.preventDefault();
        setAiPanelCollapsed(false);
        setActiveAiTab("chat");
        queueMicrotask(() => {
          promptRef.current?.focus();
        });
        logInfo("cmdk_open", {
          promptLength: promptRef.current?.value.length ?? 0,
          isProjectMode
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isProjectMode]);

  useEffect(() => {
    const hasPreview = !!(diff || newTablesPreview.length > 0);
    if (!hasPreview) {
      diffPreviewLoggedRef.current = false;
      return;
    }
    if (diffPreviewLoggedRef.current) {
      return;
    }
    diffPreviewLoggedRef.current = true;
    logInfo("diff_preview_shown", {
      hasNewTables: newTablesPreview.length > 0,
      addedColumnsCount: diff?.addedColumns?.length ?? 0,
      modifiedColumnsCount: diff?.modifiedColumns?.length ?? 0
    });
  }, [diff, newTablesPreview]);

  function chatMessagesToAgentHistory(): { role: "user" | "assistant"; content: string }[] {
    return buildAgentHistoryForRequest(workspaceMemoryRef.current, chatMessages);
  }

  function recordAppliedPlan(plan: Plan, promptText?: string, diffSnapshot: Diff | null = diff) {
    const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
    const entry = createApplyLogEntry({
      prompt: promptText?.trim() || "",
      plan,
      diff: diffSnapshot,
      modelTag: buildModelTag(modelSource, activeModelId),
      tableNames: Object.keys(tables)
    });
    const next = appendApplyLogEntry(workspaceMemoryRef.current, entry);
    workspaceMemoryRef.current = next;
    setApplyLog(next.applyLog);
    setAppliedPlansSummary(next.appliedPlansSummary);
    if (activeWorkspaceKey) {
      saveWorkspaceMemory(activeWorkspaceKey, next);
      scheduleSessionMemorySync(next, activeWorkspaceKey);
    }
  }

  function summarizePlanForChat(p: Plan | null): string {
    if (!p) return "";
    const lines: string[] = [];
    if (p.intent) {
      lines.push(p.intent);
    }
    const steps = Array.isArray(p.steps) ? p.steps : [];
    const maxSteps = 5;
    steps.slice(0, maxSteps).forEach((step, idx) => {
      const n = idx + 1;
      const action = (step as { action?: string }).action ?? "step";
      let desc = "";
      // 这里按 action 类型提取尽量简洁的中文描述，便于在 Chat 气泡中快速浏览。
      if (action === "add_column") {
        const s = step as { name?: string; expression?: string; table?: string };
        desc = `在表 ${s.table || "当前表"} 中新增列 ${s.name || ""} = ${s.expression || ""}`;
      } else if (action === "transform_column") {
        const s = step as { column?: string; transform?: string; table?: string };
        desc = `在表 ${s.table || "当前表"} 上对列 ${s.column || ""} 执行 ${s.transform || ""} 转换`;
      } else if (action === "join_tables") {
        const s = step as {
          left?: string;
          right?: string;
          leftKey?: string;
          rightKey?: string;
          resultTable?: string;
        };
        desc = `将表 ${s.left || ""} 与 ${s.right || ""} 按 ${s.leftKey || ""}=${s.rightKey || ""} 进行 join，输出到 ${
          s.resultTable || ""
        }`;
      } else if (action === "create_table") {
        const s = step as { name?: string; source?: string };
        desc = `基于 ${s.source || "现有表"} 创建新表 ${s.name || ""}`;
      } else if (action === "validate_table") {
        const s = step as { rules?: string[]; level?: string; table?: string };
        desc = `校验 ${s.table || "当前表"}：${(s.rules || []).length} 条规则（${s.level || "warn"}）`;
      } else if (action === "pivot_table") {
        const s = step as { source?: string; resultTable?: string };
        desc = `透视表 ${s.source || ""} → ${s.resultTable || ""}`;
      } else if (action === "unpivot_table") {
        const s = step as { source?: string; resultTable?: string };
        desc = `逆透视 ${s.source || ""} → ${s.resultTable || ""}`;
      } else if (action === "delete_column") {
        const s = step as { column?: string; table?: string };
        desc = `删除列 ${s.column || ""}（表 ${s.table || "当前"}）`;
      } else if (action === "reorder_columns") {
        const s = step as { columns?: string[]; table?: string };
        desc = `重排列顺序：${(s.columns || []).join(", ")}（表 ${s.table || "当前"}）`;
      } else {
        desc = JSON.stringify(step);
      }
      lines.push(`${n}. ${desc}`);
    });
    if (steps.length > maxSteps) {
      lines.push(`… 还有 ${steps.length - maxSteps} 步未展开`);
    }
    return lines.join("\n");
  }

  function appendChatMessagesFromPlan(promptText: string, nextPlan: Plan | null) {
    if (!promptText) {
      return;
    }
    const chatSessionId = serverBootId ?? "unknown";
    const now = new Date();
    const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
    const modelTag = buildModelTag(modelSource, activeModelId);
    const messageMeta = {
      modelSource,
      modelId: activeModelId,
      modelTag
    };
    const userMessage: ChatMessage = {
      id: `live-${chatSessionId}-${now.getTime()}-user`,
      sessionId: chatSessionId,
      role: "user",
      content: promptText,
      createdAt: now.toISOString(),
      projectId: projectId ?? undefined,
      source: "live",
      meta: messageMeta
    };
    const assistantContent =
      nextPlan && nextPlan.steps?.length
        ? summarizePlanForChat(nextPlan)
        : "已生成 Plan，但内容为空或无法摘要。";
    const assistantMessage: ChatMessage = {
      id: `live-${chatSessionId}-${now.getTime()}-assistant`,
      sessionId: chatSessionId,
      role: "assistant",
      content: assistantContent,
      createdAt: new Date(now.getTime() + 1).toISOString(),
      projectId: projectId ?? undefined,
      source: "live",
      meta: messageMeta
    };
    setChatMessages((prev) => [...prev, userMessage, assistantMessage]);
  }

  function appendClarificationChatMessage(clarification: {
    question: string;
    options?: string[] | null;
    context?: string | null;
  }) {
    const chatSessionId = serverBootId ?? "unknown";
    const now = new Date();
    const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
    const modelTag = buildModelTag(modelSource, activeModelId);
    const assistantMessage: ChatMessage = {
      id: `live-${chatSessionId}-${now.getTime()}-assistant-clarify`,
      sessionId: chatSessionId,
      role: "assistant",
      content: clarification.question,
      createdAt: now.toISOString(),
      projectId: projectId ?? undefined,
      source: "live",
      meta: {
        kind: "clarification",
        options: clarification.options ?? undefined,
        context: clarification.context ?? undefined,
        modelSource,
        modelId: activeModelId,
        modelTag
      }
    };
    setChatMessages((prev) => [...prev, assistantMessage]);
  }

  function appendClarificationUserMessage(answer: string) {
    const chatSessionId = serverBootId ?? "unknown";
    const now = new Date();
    const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
    const modelTag = buildModelTag(modelSource, activeModelId);
    const userMessage: ChatMessage = {
      id: `live-${chatSessionId}-${now.getTime()}-user-clarify`,
      sessionId: chatSessionId,
      role: "user",
      content: answer,
      createdAt: now.toISOString(),
      projectId: projectId ?? undefined,
      source: "live",
      meta: {
        kind: "clarification_answer",
        modelSource,
        modelId: activeModelId,
        modelTag
      }
    };
    setChatMessages((prev) => [...prev, userMessage]);
  }

  function receiveAgentClarification(
    clarification: {
      question: string;
      options?: string[] | null;
      context?: string | null;
    },
    originalPrompt: string,
    traceId: string,
    source: PendingClarificationSource
  ) {
    setPlan(null);
    setDiff(null);
    setNewTablesPreview([]);
    if (source === "generate") {
      setPendingServerPreviewId(null);
    }
    setPendingClarification({
      question: clarification.question,
      options: clarification.options,
      context: clarification.context,
      originalPrompt,
      traceId,
      source
    });
    appendClarificationChatMessage(clarification);
    workspaceMemoryRef.current = appendClarificationQuestionToTranscript(
      workspaceMemoryRef.current,
      clarification.question,
      clarification.context
    );
    if (activeWorkspaceKey) {
      debouncedSaveWorkspaceMemory(activeWorkspaceKey, workspaceMemoryRef.current);
    }
    setStatus(
      `需要澄清：${clarification.question}` +
        (clarification.options?.length
          ? ` 选项：${clarification.options.join(" / ")}`
          : "")
    );
    setPrompt("");
    logInfo("agent_clarification", {
      traceId,
      source,
      optionsCount: clarification.options?.length ?? 0,
      hasContext: Boolean(clarification.context?.trim()),
      questionPreview: clarification.question.slice(0, 120)
    });
    logInfo("plan_response", {
      traceId,
      success: true,
      stepsCount: 0,
      mode: "agent_clarification"
    });
    const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
    setConversations((prev) => {
      const nextId = (prev[0]?.id ?? 0) + 1;
      const entry = buildClarificationTechnicalHistoryEntry(clarification, {
        nextId,
        prompt: originalPrompt,
        requestPayload: { prompt: originalPrompt, traceId },
        modelSource,
        modelId: activeModelId,
        modelTag: buildModelTag(modelSource, activeModelId)
      });
      return [entry as ConversationEntry, ...prev];
    });
  }

  async function submitClarificationAnswer(answer: string) {
    const pending = pendingClarification;
    if (!pending) {
      return;
    }
    const trimmed = answer.trim();
    if (!trimmed) {
      setStatus("请先选择上方选项，或在输入框中填写表名/简短回答。");
      return;
    }

    const traceId = generateTraceId();
    appendClarificationUserMessage(trimmed);
    workspaceMemoryRef.current = appendClarificationAnswerToTranscript(
      workspaceMemoryRef.current,
      trimmed
    );
    if (activeWorkspaceKey) {
      debouncedSaveWorkspaceMemory(activeWorkspaceKey, workspaceMemoryRef.current);
    }
    setPrompt("");
    setStatus(modelSource === "cloud" ? "Calling cloud LLM…" : "Calling local LLM…");

    const resumeHistory = buildAgentHistoryForRequest(
      workspaceMemoryRef.current,
      chatMessages
    );

    try {
      const tablesArr = Object.values(tables);
      const usingProjectApi = !!projectId;
      const agentRes = await requestAgentProjectPlan({
        prompt: pending.originalPrompt,
        clarificationReply: trimmed,
        clarificationTurnId: pending.traceId,
        tables: tablesArr,
        modelSource,
        cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
        localModelId: modelSource === "local" ? localModelId : undefined,
        traceId,
        sessionId: workspaceSessionId ?? undefined,
        history: resumeHistory,
        appliedPlansSummary,
        previewLifecycle: true,
        projectId: usingProjectApi ? projectId : undefined,
        previewTables: usingProjectApi ? undefined : tablesArr,
        previewHistory: agentPreviewHistory,
        revisionCount: agentRevisionCount,
        signal: takeAgentLlmAbortSignal(),
        context: buildAgentContextPayload()
      });

      if (agentRes.kind === "clarification") {
        receiveAgentClarification(
          agentRes.clarification,
          pending.originalPrompt,
          traceId,
          pending.source
        );
        return;
      }

      setPendingClarification(null);

      if (agentRes.kind === "plan") {
        const nextPlan = agentRes.plan;
        setPendingServerPreviewId(null);
        setPlan(nextPlan);
        logInfo("clarification_resolved", {
          traceId,
          clarificationTurnId: pending.traceId,
          answerLength: trimmed.length,
          resultKind: agentRes.kind,
          success: true
        });
        logInfo("plan_response", {
          traceId,
          success: true,
          stepsCount: nextPlan.steps.length,
          mode: "agent_clarification_resolved_plan"
        });
        const preview = applyProjectPlan(tables, nextPlan);
        setDiff(preview.diff);
        setNewTablesPreview(preview.newTables);
        appendChatMessagesFromPlan(resumePrompt, nextPlan);
        setStatus("Plan generated. Review Diff, then Apply.");
        return;
      }

      if (agentRes.kind === "preview_ready") {
        const nextPlan = agentRes.plan;
        const localPreview = applyProjectPlan(tables, nextPlan);
        setPlan(nextPlan);
        setDiff(agentRes.preview.diff ?? localPreview.diff);
        setNewTablesPreview(
          agentRes.preview.newTables.length > 0
            ? agentRes.preview.newTables
            : localPreview.newTables
        );
        setAgentPreviewHistory(agentRes.previewHistory);
        setPendingServerPreviewId(agentRes.preview.id);
        const st = agentRes.state;
        const rc = Number(st.revision_count ?? st.revisionCount ?? 0);
        setAgentRevisionCount(Number.isFinite(rc) ? rc : 0);
        logInfo("clarification_resolved", {
          traceId,
          clarificationTurnId: pending.traceId,
          answerLength: trimmed.length,
          resultKind: agentRes.kind,
          success: true
        });
        logInfo("plan_response", {
          traceId,
          success: true,
          stepsCount: nextPlan.steps.length,
          mode: "agent_clarification_resolved_preview"
        });
        appendChatMessagesFromPlan(resumePrompt, nextPlan);
        setStatus(previewReadyStatusMessage(agentRes.warnings, agentRes.summary));
        return;
      }

      logInfo("clarification_resolved", {
        traceId,
        clarificationTurnId: pending.traceId,
        answerLength: trimmed.length,
        success: false
      });
      setStatus("Unexpected agent response after clarification.");
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      const { technical } = splitApiErrorDetail(msg);
      logInfo("clarification_resolved", {
        traceId,
        clarificationTurnId: pending.traceId,
        answerLength: trimmed.length,
        success: false
      });
      logError("clarification_resume_error", {
        traceId,
        message: msg,
        ...(technical ? { technicalDetail: technical } : {})
      });
      setStatus(statusFromErrorMessage(msg));
    }
  }

  async function onGenerate() {
    if (pendingClarification) {
      await submitClarificationAnswer(prompt.trim());
      return;
    }

    const traceId = generateTraceId();
    const modelId = modelSource === "cloud" ? cloudModelId : localModelId;
    logInfo("cmdk_prompt_submit", {
      traceId,
      promptLength: prompt.length,
      isProjectMode,
      modelSource,
      modelId,
      projectId: projectId ?? undefined,
      planMode: isProjectMode
        ? projectId
          ? "project_id"
          : "project_tables"
        : "single_table"
    });
    setStatus(modelSource === "cloud" ? "Calling cloud LLM…" : "Calling local LLM…");
    try {
      const tablesArr = Object.values(tables);
      const usingProjectApi = !!projectId;
      setAgentPreviewHistory([]);
      setAgentRevisionCount(0);
      setPendingServerPreviewId(null);
      lastAgentPlanPromptRef.current = prompt;
      const requestPayload = {
        mode: "agent_preview" as const,
        prompt,
        projectId: usingProjectApi ? projectId : undefined,
        tablesSample: tablesArr.map((t) => ({
          name: t.name,
          sampleRows: t.rows.slice(0, 10)
        })),
        modelSource,
        cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
        localModelId: modelSource === "local" ? localModelId : undefined
      };
      const agentOpts = {
        prompt,
        tables: tablesArr,
        modelSource,
        cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
        localModelId: modelSource === "local" ? localModelId : undefined,
        traceId,
        sessionId: workspaceSessionId ?? undefined,
        history: chatMessagesToAgentHistory(),
        appliedPlansSummary,
        previewLifecycle: true,
        projectId: usingProjectApi ? projectId : undefined,
        previewTables: usingProjectApi ? undefined : tablesArr,
        previewHistory: agentPreviewHistory,
        revisionCount: agentRevisionCount,
        signal: takeAgentLlmAbortSignal(),
        context: buildAgentContextPayload()
      };
      const useAgentStream = import.meta.env.VITE_AGENT_USE_STREAM === "true";
      const agentRes = useAgentStream
        ? await requestAgentProjectPlanViaStream(agentOpts)
        : await requestAgentProjectPlan(agentOpts);
      if (agentRes.kind === "clarification") {
        receiveAgentClarification(agentRes.clarification, prompt, traceId, "generate");
        return;
      }
      if (agentRes.kind === "plan") {
        const nextPlan = agentRes.plan;
        setPendingServerPreviewId(null);
        setPlan(nextPlan);
        logInfo("plan_response", {
          traceId,
          success: true,
          stepsCount: nextPlan.steps.length,
          mode: usingProjectApi ? "project_id_agent" : "project_tables_agent"
        });
        const preview = applyProjectPlan(tables, nextPlan);
        setDiff(preview.diff);
        setNewTablesPreview(preview.newTables);
        appendChatMessagesFromPlan(prompt, nextPlan);
        setConversations((prev) => {
          const nextId = (prev[0]?.id ?? 0) + 1;
          const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
          const entry: ConversationEntry = {
            id: nextId,
            prompt,
            payload: requestPayload,
            plan: nextPlan,
            diff: preview.diff,
            createdAt: new Date().toLocaleString(),
            modelSource,
            modelId: activeModelId,
            modelTag: buildModelTag(modelSource, activeModelId)
          };
          return [entry, ...prev];
        });
        setStatus("Plan generated. Review Diff, then Apply.");
        return;
      }
      if (agentRes.kind === "preview_ready") {
        const nextPlan = agentRes.plan;
        const localPreview = applyProjectPlan(tables, nextPlan);
        setPlan(nextPlan);
        setDiff(agentRes.preview.diff ?? localPreview.diff);
        setNewTablesPreview(
          agentRes.preview.newTables.length > 0
            ? agentRes.preview.newTables
            : localPreview.newTables
        );
        setAgentPreviewHistory(agentRes.previewHistory);
        setPendingServerPreviewId(agentRes.preview.id);
        const st = agentRes.state;
        const rc = Number(st.revision_count ?? st.revisionCount ?? 0);
        setAgentRevisionCount(Number.isFinite(rc) ? rc : 0);
        logInfo("plan_response", {
          traceId,
          success: true,
          stepsCount: nextPlan.steps.length,
          mode: "agent_preview_ready"
        });
        appendChatMessagesFromPlan(prompt, nextPlan);
        setConversations((prev) => {
          const nextId = (prev[0]?.id ?? 0) + 1;
          const activeModelId = modelSource === "cloud" ? cloudModelId : localModelId;
          const entry: ConversationEntry = {
            id: nextId,
            prompt,
            payload: requestPayload,
            plan: nextPlan,
            diff: agentRes.preview.diff,
            createdAt: new Date().toLocaleString(),
            modelSource,
            modelId: activeModelId,
            modelTag: buildModelTag(modelSource, activeModelId)
          };
          return [entry, ...prev];
        });
        setStatus(previewReadyStatusMessage(agentRes.warnings, agentRes.summary));
        return;
      }
      setStatus("Unexpected agent response.");
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      const { technical } = splitApiErrorDetail(msg);
      logError("plan_request_error", {
        traceId,
        message: msg,
        ...(technical ? { technicalDetail: technical } : {})
      });
      setStatus(statusFromErrorMessage(msg));
    }
  }

  function onUndo() {
    if (history.length === 0) {
      setStatus("Nothing to undo.");
      return;
    }
    logInfo("undo_click", { historyLength: history.length });
    const last = history[history.length - 1];
    setTables(clone(last));
    setHistory((h) => h.slice(0, -1));
    setStatus("Undone last apply.");
  }

  function onApply() {
    if (!plan) return;
    const traceId = generateTraceId();
    logInfo("plan_apply_click", {
      traceId,
      stepsCount: plan.steps.length,
      useProjectApi: !!projectId && isProjectMode,
      projectId: projectId ?? undefined,
      serverPreview: !!pendingServerPreviewId
    });
    setHistory((h) => [...h, clone(tables)]);

    (async () => {
      try {
        if (pendingServerPreviewId && isProjectMode) {
          const tablesArr = Object.values(tables);
          const usingProjectApi = !!projectId;
          const res = await requestAgentProjectPlan({
            prompt: lastAgentPlanPromptRef.current || prompt,
            tables: tablesArr,
            modelSource,
            cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
            localModelId: modelSource === "local" ? localModelId : undefined,
            traceId,
            sessionId: workspaceSessionId ?? undefined,
            history: chatMessagesToAgentHistory(),
            appliedPlansSummary,
            previewLifecycle: true,
            projectId: usingProjectApi ? projectId : undefined,
            previewTables: usingProjectApi ? undefined : tablesArr,
            previewHistory: agentPreviewHistory,
            revisionCount: agentRevisionCount,
            previewDecision: "confirm",
            previewId: pendingServerPreviewId,
            commitPlan: plan,
            signal: takeAgentLlmAbortSignal(),
            context: buildAgentContextPayload()
          });
          if (res.kind !== "committed") {
            setStatus("确认失败：服务器未返回 committed。");
            return;
          }
          recordAppliedPlan(
            plan,
            lastAgentPlanPromptRef.current || prompt,
            res.executeResult.diff
          );
          setTables(res.executeResult.tables);
          if (res.executeResult.newTables.length > 0) {
            setActiveTable(res.executeResult.newTables[0]!);
          }
          setAgentPreviewHistory(res.previewHistory);
          setPendingServerPreviewId(null);
          setPlan(null);
          setDiff(null);
          setNewTablesPreview([]);
          setPrompt("");
          setStatus("已通过服务器预览确认并写回。");
          logInfo("plan_apply_success", {
            traceId,
            newTablesCount: res.executeResult.newTables.length,
            mode: "server_preview_confirm"
          });
          return;
        }

        const useProjectApi = !!projectId && isProjectMode;
        const result = useProjectApi
          ? await executeProjectPlanById({
              projectId: projectId!,
              plan,
              traceId
            })
          : await executePlanOnServer({ tables, plan, traceId });
        recordAppliedPlan(plan, lastAgentPlanPromptRef.current || prompt, result.diff);
        setTables(result.tables);
        if (result.newTables.length > 0) {
          setActiveTable(result.newTables[0]);
        }
        setStatus("Applied by backend.");
        setPrompt("");
        setPlan(null);
        setDiff(null);
        setNewTablesPreview([]);
        logInfo("plan_apply_success", {
          traceId,
          newTablesCount: result.newTables.length
        });
      } catch (e: unknown) {
        const em = String((e as Error)?.message ?? e);
        setStatus("Apply failed: " + em);
        logError("plan_apply_error", { traceId, message: em });
      }
    })();
  }

  async function onAbortServerPreview() {
    if (!pendingServerPreviewId) return;
    const traceId = generateTraceId();
    try {
      const tablesArr = Object.values(tables);
      const usingProjectApi = !!projectId;
      const res = await requestAgentProjectPlan({
        prompt: lastAgentPlanPromptRef.current || prompt,
        tables: tablesArr,
        modelSource,
        cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
        localModelId: modelSource === "local" ? localModelId : undefined,
        traceId,
        sessionId: workspaceSessionId ?? undefined,
        history: chatMessagesToAgentHistory(),
        appliedPlansSummary,
        previewLifecycle: true,
        projectId: usingProjectApi ? projectId : undefined,
        previewTables: usingProjectApi ? undefined : tablesArr,
        previewHistory: agentPreviewHistory,
        revisionCount: agentRevisionCount,
        previewDecision: "abort",
        previewId: pendingServerPreviewId,
        signal: takeAgentLlmAbortSignal(),
        context: buildAgentContextPayload()
      });
      if (res.kind !== "preview_aborted") {
        setStatus("Abort 失败：响应异常。");
        return;
      }
      setAgentPreviewHistory(res.previewHistory);
      setPendingServerPreviewId(null);
      setPlan(null);
      setDiff(null);
      setNewTablesPreview([]);
      setStatus("已放弃预览，表格未修改；记录保留在历史中。");
      logInfo("preview_abort", { traceId });
    } catch (e: unknown) {
      const em = String((e as Error)?.message ?? e);
      setStatus("Abort failed: " + em);
      logError("preview_abort_error", { traceId, message: em });
    }
  }

  async function onReviseServerPreview() {
    if (!pendingServerPreviewId) return;
    const extra = prompt.trim();
    if (!extra) {
      setStatus("请先在输入框中填写修订说明，再点 Revise。");
      return;
    }
    const traceId = generateTraceId();
    setStatus(modelSource === "cloud" ? "Calling cloud LLM…" : "Calling local LLM…");
    try {
      const tablesArr = Object.values(tables);
      const usingProjectApi = !!projectId;
      const agentRes = await requestAgentProjectPlan({
        prompt: lastAgentPlanPromptRef.current || prompt,
        tables: tablesArr,
        modelSource,
        cloudModelId: modelSource === "cloud" ? cloudModelId : undefined,
        localModelId: modelSource === "local" ? localModelId : undefined,
        traceId,
        sessionId: workspaceSessionId ?? undefined,
        history: chatMessagesToAgentHistory(),
        appliedPlansSummary,
        previewLifecycle: true,
        projectId: usingProjectApi ? projectId : undefined,
        previewTables: usingProjectApi ? undefined : tablesArr,
        previewHistory: agentPreviewHistory,
        revisionCount: agentRevisionCount,
        previewDecision: "revise",
        previewId: pendingServerPreviewId,
        revisionMessage: extra,
        signal: takeAgentLlmAbortSignal(),
        context: buildAgentContextPayload()
      });
      if (agentRes.kind === "preview_ready") {
        const nextPlan = agentRes.plan;
        const localPreview = applyProjectPlan(tables, nextPlan);
        setPlan(nextPlan);
        setDiff(agentRes.preview.diff ?? localPreview.diff);
        setNewTablesPreview(
          agentRes.preview.newTables.length > 0
            ? agentRes.preview.newTables
            : localPreview.newTables
        );
        setAgentPreviewHistory(agentRes.previewHistory);
        setPendingServerPreviewId(agentRes.preview.id);
        const st = agentRes.state;
        const rc = Number(st.revision_count ?? st.revisionCount ?? 0);
        setAgentRevisionCount(Number.isFinite(rc) ? rc : 0);
        appendChatMessagesFromPlan(extra, agentRes.plan);
        {
          const reviseBase = agentRes.warnings?.length
            ? previewReadyStatusMessage(agentRes.warnings)
            : "已根据修订生成新的服务器预览。";
          setStatus(
            agentRes.summary
              ? `${reviseBase} AI 摘要：${agentRes.summary}（AI 生成，可能有误，请以 Diff 为准）`
              : reviseBase
          );
        }
        logInfo("preview_revise", { traceId });
        return;
      }
      if (agentRes.kind === "plan") {
        setPendingServerPreviewId(null);
        setPlan(agentRes.plan);
        const preview = applyProjectPlan(tables, agentRes.plan);
        setDiff(preview.diff);
        setNewTablesPreview(preview.newTables);
        setStatus("修订后返回普通 Plan（无服务端预览副本），可本地 Apply。");
        return;
      }
      if (agentRes.kind === "clarification") {
        receiveAgentClarification(
          agentRes.clarification,
          lastAgentPlanPromptRef.current || prompt,
          traceId,
          "preview_revise"
        );
        return;
      }
      setStatus("修订响应异常。");
    } catch (e: unknown) {
      const msg = String((e as Error)?.message ?? e);
      setStatus(statusFromErrorMessage(msg));
      logError("preview_revise_error", { traceId, message: msg });
    }
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
    const traceId = generateTraceId();
    logInfo("export_excel_click", {
      traceId,
      tableCount: Object.keys(tables).length
    });
    setStatus("正在导出…");
    try {
      const tablesArr = Object.values(tables);
      const blob = await exportProjectToExcel(tablesArr, { traceId });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "project.xlsx";
      a.click();
      URL.revokeObjectURL(url);
      setStatus("已下载 project.xlsx");
      logInfo("export_excel_success", { traceId });
    } catch (e: unknown) {
      const em = String((e as Error)?.message ?? e);
      setStatus("导出失败: " + em);
      logError("export_excel_error", { traceId, message: em });
    }
  }

  const placeholder = pendingClarification
    ? "选择上方选项，或输入表名/简短回答（不必重写整条指令）"
    : isProjectMode
      ? '例：从产品信息 lookup 类别到销售订单（产品 ↔ 产品名称）'
      : '试：在销售订单表新增金额 = 数量 * 单价';

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
        <div style={{ fontWeight: 600, display: "flex", alignItems: "center" }}>
          <i className="bi bi-table header-title-icon" aria-hidden="true" />
          Cursor for Spreadsheet — 多表项目
        </div>
        <div className="small" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span>
            <span className="kbd">Cmd</span>+<span className="kbd">K</span> 聚焦 AI 面板
          </span>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }} className="small header-status-cluster">
          {loadSampleLoading && (
            <span style={{ display: "flex", alignItems: "center", gap: 6 }} aria-live="polite">
              <i
                className="bi bi-arrow-repeat bi-spin header-status-icon"
                aria-hidden="true"
              />
              正在加载示例…
            </span>
          )}
          {loadSampleError && !loadSampleLoading && (
            <button
              type="button"
              className="btn header-inline-btn"
              onClick={loadSampleTables}
              title={loadSampleError}
            >
              <i className="bi bi-folder2-open" aria-hidden="true" />
              加载示例
            </button>
          )}
          <span>{status}</span>
        </div>
      </div>

      <div className="toolbar">
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn" title="撤销" onClick={onUndo} disabled={history.length === 0}>
            <i className="bi bi-arrow-counterclockwise" aria-hidden="true" />
          </button>
          <button type="button" className="toolbar-btn" title="重做" disabled>
            <i className="bi bi-arrow-clockwise" aria-hidden="true" />
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
            <i className="bi bi-type-bold" aria-hidden="true" />
          </button>
          <button type="button" className="toolbar-btn" title="斜体" onClick={onToolbarItalic}>
            <i className="bi bi-type-italic" aria-hidden="true" />
          </button>
          <button type="button" className="toolbar-btn" title="下划线" onClick={onToolbarUnderline}>
            <i className="bi bi-type-underline" aria-hidden="true" />
          </button>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="左对齐" onClick={() => onToolbarAlign("left")}>
            <i className="bi bi-text-left" aria-hidden="true" />
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="居中" onClick={() => onToolbarAlign("center")}>
            <i className="bi bi-text-center" aria-hidden="true" />
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="右对齐" onClick={() => onToolbarAlign("right")}>
            <i className="bi bi-text-right" aria-hidden="true" />
          </button>
        </div>
        {/* 填充颜色、添加行、添加列
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn" title="填充颜色" onClick={onToolbarBgColor}>
            <i className="bi bi-palette-fill" aria-hidden="true" />
          </button>
        </div>
        <div className="toolbar-divider" />
        <div className="toolbar-group">
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="添加行" onClick={onAddRow}>
            <i className="bi bi-list-ul" aria-hidden="true" />
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-icon" title="添加列" onClick={onAddColumn}>
            <i className="bi bi-layout-sidebar-inset-reverse" aria-hidden="true" />
          </button>
        </div>
        */}
        <div className="toolbar-group" style={{ marginLeft: "auto" }}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            style={{ display: "none" }}
            onChange={onFileChange}
          />
          <button
            type="button"
            className="toolbar-btn toolbar-btn-text"
            title="导入 Excel/CSV 文件"
            onClick={onImportClick}
            disabled={importLoading}
            aria-busy={importLoading}
          >
            <i
              className={`bi ${
                importLoading ? "bi-arrow-repeat bi-spin" : "bi-file-earmark-arrow-up"
              }`}
              aria-hidden="true"
            />
            {importLoading ? "导入中…" : "导入文件"}
          </button>
          <button type="button" className="toolbar-btn toolbar-btn-text" title="下载为 Excel" onClick={onDownloadExcel}>
            <i className="bi bi-file-earmark-arrow-down" aria-hidden="true" />
            导出文件
          </button>
        </div>
      </div>

      <div className="tabs-row">
        {displayTableNames.map((name) => (
          <div key={name} className={`tab ${activeTable === name ? "active" : ""}`}>
            <button
              type="button"
              className="tab-btn"
              title={`切换到 ${name}`}
              onClick={() => setActiveTable(name)}
            >
              {name}
            </button>
            {!isPreviewMode && tableNames.length > 1 && (
              <button
                type="button"
                className="tab-close"
                onClick={() => onRemoveTable(name)}
                title="删除表"
                aria-label="删除表"
              >
                <i className="bi bi-x-lg" aria-hidden="true" />
              </button>
            )}
          </div>
        ))}
        <button type="button" className="btn tab-add" title="添加新表" onClick={onAddTable}>
          <i className="bi bi-plus-lg" aria-hidden="true" />
          新表
        </button>
      </div>

      <div className="container">
        <div className="grid ag-theme-quartz">
          {currentDisplayTable && (
            <AgGridReact
              key={activeTable}
              rowData={currentDisplayTable.rows}
              columnDefs={colDefs}
              defaultColDef={{
                resizable: true,
                sortable: true,
                filter: true,
                editable: !isPreviewMode
              }}
              rowSelection="multiple"
              onGridReady={(e) => {
                gridRef.current = e.api;
              }}
              onSelectionChanged={(e) => {
                const selectedCount = e.api.getSelectedNodes().length;
                if (selectedCount > 0) {
                  setGridSelectionSummary(`已选 ${selectedCount} 行`);
                  return;
                }
                const focused = e.api.getFocusedCell();
                if (focused?.column) {
                  const colId = focused.column.getColId();
                  if (colId && colId !== "__rowNum") {
                    setGridSelectionSummary(`列 ${colId}`);
                    return;
                  }
                }
                setGridSelectionSummary(null);
              }}
              onCellValueChanged={(e) => {
                if (isPreviewMode || !currentTable) return;
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
            {activeAiTab === "schema" ? (
              <div className="panel-section schema-section schema-tab-panel">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>
                  Schema {isProjectMode && currentDisplayTable ? `(${activeTable})` : ""}
                </div>
                {!currentDisplayTable ? (
                  <div className="small">暂无当前表，无法展示 schema。</div>
                ) : (
                  <>
                    <pre>{JSON.stringify(currentDisplayTable.schema, null, 2)}</pre>
                    <div className="small">
                      {isProjectMode
                        ? "项目内所有表的 schema 和部分行数据会发送给 LLM。"
                        : "This schema and a subset of rows are what the LLM sees."}
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="panel-section ai-panel">
                <div style={{ fontWeight: 600, marginBottom: 8 }}>AI Edit</div>

            {activeAiTab === "chat" && (
              <>
                {isProjectMode && (
                  <div className="small" style={{ color: "#0066cc" }}>
                    项目模式：可对多张表进行 join / create_table 等操作
                  </div>
                )}
                {showBackendRestartBanner && (
                  <div className="backend-restart-banner small">
                    后端已重启；对话已从工作区本地记忆恢复。
                  </div>
                )}
                <div className="chat-history-container" ref={chatScrollRef}>
                  {chatMessages.length === 0 && (
                    <div className="small chat-empty-hint">
                      暂无对话，可在下方输入指令开始与 AI 交互（按工作区保存在浏览器中，后端重启后仍可恢复）。
                    </div>
                  )}
                  {chatMessages.map((msg) => (
                    <div
                      key={msg.id}
                      className={`chat-message-row chat-message-${msg.role} chat-message-${msg.source}`}
                    >
                      <div className="chat-bubble">
                        <div className="chat-meta small">
                          <span className={`chat-meta-${msg.role.toLowerCase()}`}>
                            {msg.role === "user"
                              ? "你"
                              : msg.role === "assistant"
                              ? "AI"
                              : "系统"}
                          </span>
                          <span className={`chat-meta-time chat-meta-${msg.role.toLowerCase()}`}>
                            {new Date(msg.createdAt).toLocaleString()}
                          </span>
                          {msg.meta?.modelTag && (
                            <span className="chat-tag">{msg.meta.modelTag}</span>
                          )}
                          {msg.source === "history" && (
                            <span className="chat-tag">历史</span>
                          )}
                        </div>
                        <div className="chat-content">
                          {msg.meta?.kind === "clarification" ? (
                            <ClarificationBubble
                              question={msg.content}
                              options={
                                Array.isArray(msg.meta.options)
                                  ? (msg.meta.options as string[])
                                  : undefined
                              }
                              context={
                                typeof msg.meta.context === "string"
                                  ? msg.meta.context
                                  : undefined
                              }
                              onSelectOption={submitClarificationAnswer}
                            />
                          ) : (
                            msg.content || "(空)"
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="ai-prompt-dock">
                  {SHOW_MODEL_SWITCH && (
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
                    <details className="custom-model-panel">
                      <summary>自定义模型</summary>
                      <div className="custom-model-form">
                        <input
                          className="custom-model-input"
                          value={customModelId}
                          onChange={(e) => {
                            setCustomModelId(e.target.value);
                            setCustomModelError("");
                          }}
                          placeholder={
                            modelSource === "cloud" ? "如 anthropic/claude-opus-4" : "如 llama3.1:8b"
                          }
                          aria-label="自定义模型 ID"
                        />
                        <input
                          className="custom-model-input"
                          value={customModelLabel}
                          onChange={(e) => setCustomModelLabel(e.target.value)}
                          placeholder="显示名（可选）"
                          aria-label="自定义模型显示名"
                        />
                        <button type="button" onClick={handleAddCustomModel}>
                          添加
                        </button>
                      </div>
                      {customModelError && (
                        <div className="custom-model-error">{customModelError}</div>
                      )}
                      {customModels[modelSource].length > 0 && (
                        <ul className="custom-model-list">
                          {customModels[modelSource].map((m) => (
                            <li key={m.id}>
                              <span title={m.id}>{m.label}</span>
                              <button
                                type="button"
                                onClick={() => handleRemoveCustomModel(m.id)}
                                aria-label={`删除 ${m.id}`}
                              >
                                ×
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </details>
                  </div>
                  )}
                  <details className="workspace-rules-panel small">
                    <summary>Workspace rules (optional)</summary>
                    <textarea
                      className="workspace-rules-textarea"
                      value={workspaceRules}
                      onChange={(e) => setWorkspaceRules(e.target.value)}
                      placeholder="e.g. Always use ISO dates; round currency to 2 decimals."
                      rows={3}
                    />
                  </details>
                  <div className="cmdk-context-strip small" aria-label="Cmd+K context">
                    <span>表: {activeTable}</span>
                    {gridSelectionSummary ? <span> · {gridSelectionSummary}</span> : null}
                    {applyLog[0] ? (
                      <span> · 上次 Apply: {formatLastApplyHint(applyLog[0])}</span>
                    ) : null}
                  </div>
                  <div
                    className={`prompt-compose${
                      pendingClarification ? " prompt-compose--clarification-pending" : ""
                    }`}
                  >
                    {pendingClarification && (
                      <div className="clarification-prompt-anchor small">
                        原指令：{truncatePromptAnchor(pendingClarification.originalPrompt)}
                      </div>
                    )}
                    <div className="prompt-input-wrap">
                      <textarea
                        ref={promptRef}
                        className="prompt-textarea"
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        placeholder={placeholder}
                      />
                    </div>
                    <div className="prompt-generate-row">
                      <button
                        type="button"
                        className="btn primary prompt-generate-btn"
                        onClick={onGenerate}
                      >
                        {pendingClarification ? "继续生成" : "Generate Plan"}
                      </button>
                    </div>
                  </div>
                </div>

                {(diff || newTablesPreview.length > 0) && (
                  <>
                    <div style={{ fontWeight: 600 }}>Diff Preview</div>
                    <div className="small" style={{ marginBottom: 8 }}>
                      表格中为预览结果，确认后 Apply。
                      {diff &&
                        (diff.addedColumns.length > 0 || diff.modifiedColumns.length > 0) && (
                          <>
                            {" "}
                            变更列：
                            {[...diff.addedColumns, ...diff.modifiedColumns].join(", ")}
                          </>
                        )}
                    </div>
                    {newTablesPreview.length > 0 && (
                      <div className="small">
                        将新建表: {newTablesPreview.join(", ")}
                      </div>
                    )}
                    <div className="row">
                      <button className="btn primary" onClick={onApply}>
                        Apply
                      </button>
                      {pendingServerPreviewId && (
                        <>
                          <button type="button" className="btn" onClick={onAbortServerPreview}>
                            Abort
                          </button>
                          <button type="button" className="btn" onClick={onReviseServerPreview}>
                            Revise
                          </button>
                        </>
                      )}
                      <div className="small">
                        {pendingServerPreviewId
                          ? "Apply：服务端确认写回；Abort：放弃预览不修改数据；Revise：使用上方输入框中的说明请求新计划。"
                          : "Apply plan to project data."}
                      </div>
                    </div>
                  </>
                )}
              </>
            )}

            {activeAiTab === "history" && (
              <div className="conversation-section">
                <div style={{ fontWeight: 600 }}>历史记录（技术视图）</div>
                <div className="small" style={{ marginBottom: 6 }}>
                  此处用于查看每次调用 LLM 的原始 payload / plan / diff，适合调试；
                  自然语言对话请在「AI 对话」标签中查看。
                </div>
                {conversations.length === 0 ? (
                  <div className="small">暂时还没有历史记录。</div>
                ) : (
                  <div className="conversation-list">
                    {conversations.map((item) => (
                      <div key={item.id} className="conversation-item">
                        <div className="conversation-header">
                          <div className="small">
                            #{item.id} ·{" "}
                            {item.modelTag ??
                              formatModelTag(item.modelSource, item.modelId)}
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
                        {expandedResponseIds.has(item.id) && item.mode === "agent_clarification" && item.clarification && (
                          <>
                            <div style={{ fontWeight: 600, marginTop: 4 }}>Clarification</div>
                            <div className="small" style={{ marginTop: 4 }}>
                              <strong>Question：</strong> {item.clarification.question}
                            </div>
                            {item.clarification.options && item.clarification.options.length > 0 && (
                              <div className="small" style={{ marginTop: 4 }}>
                                <strong>Options：</strong>{" "}
                                {item.clarification.options.join(" / ")}
                              </div>
                            )}
                            {item.clarification.context && (
                              <>
                                <div style={{ fontWeight: 600, marginTop: 8 }}>Context</div>
                                <pre>{item.clarification.context}</pre>
                              </>
                            )}
                          </>
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
                )}
              </div>
            )}
              </div>
            )}
          </div>
          <div
            className="panel-edge-tabs"
            role="tablist"
            aria-label="AI panel views"
          >
            <button
              type="button"
              role="tab"
              aria-selected={activeAiTab === "chat"}
              className={`panel-edge-tab ${activeAiTab === "chat" ? "active" : ""}`}
              title="AI 对话"
              aria-label="AI 对话"
              onClick={() => {
                setAiPanelCollapsed(false);
                setActiveAiTab("chat");
                logInfo("ai_edge_tab_select", { tab: "chat" });
              }}
            >
              <i className="bi bi-chat-dots" aria-hidden="true" />
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeAiTab === "schema"}
              className={`panel-edge-tab ${activeAiTab === "schema" ? "active" : ""}`}
              title="Schema"
              aria-label="Schema"
              onClick={() => {
                setAiPanelCollapsed(false);
                setActiveAiTab("schema");
                logInfo("ai_edge_tab_select", { tab: "schema" });
              }}
            >
              <i className="bi bi-braces" aria-hidden="true" />
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeAiTab === "history"}
              className={`panel-edge-tab ${activeAiTab === "history" ? "active" : ""}`}
              title="历史对话（LLM 调用调试）"
              aria-label="历史对话"
              onClick={() => {
                logInfo("history_tab_open", {});
                setAiPanelCollapsed(false);
                setActiveAiTab("history");
                logInfo("ai_edge_tab_select", { tab: "history" });
              }}
            >
              <i className="bi bi-clock-history" aria-hidden="true" />
            </button>
            <button
              type="button"
              className="panel-edge-tab panel-edge-tab-collapse"
              title={aiPanelCollapsed ? "展开 AI 面板" : "折叠 AI 面板"}
              aria-label={aiPanelCollapsed ? "展开 AI 面板" : "折叠 AI 面板"}
              onClick={() => {
                setAiPanelCollapsed((v) => {
                  const next = !v;
                  logInfo("ai_panel_collapse_toggle", { collapsed: next });
                  return next;
                });
              }}
            >
              <i
                className={`bi ${aiPanelCollapsed ? "bi-chevron-left" : "bi-chevron-right"}`}
                aria-hidden="true"
              />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
