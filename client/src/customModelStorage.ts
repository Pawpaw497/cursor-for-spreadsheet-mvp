import type { ConfigResponse, ModelOption, ModelSource } from "./llm";

export const CUSTOM_MODELS_STORAGE_KEY = "spreadsheet-cursor:custom-models";

const STORAGE_VERSION = 1;

/** 用户在 UI 上自行添加的模型，按来源分组。 */
export type CustomModels = {
  cloud: ModelOption[];
  local: ModelOption[];
};

type StoredCustomModelsPayload = {
  version: number;
  cloud: ModelOption[];
  local: ModelOption[];
};

export const EMPTY_CUSTOM_MODELS: CustomModels = { cloud: [], local: [] };

/** 校验并规整用户输入；id 必须非空且不含空白。 */
export function normalizeCustomModelInput(
  rawId: string,
  rawLabel: string
): ModelOption | null {
  const id = rawId.trim();
  if (!id || /\s/.test(id)) return null;
  const label = rawLabel.trim() || id;
  return { id, label };
}

function parseModelOptions(value: unknown): ModelOption[] {
  if (!Array.isArray(value)) return [];
  const out: ModelOption[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    if (typeof record.id !== "string" || typeof record.label !== "string") continue;
    const normalized = normalizeCustomModelInput(record.id, record.label);
    if (normalized) out.push(normalized);
  }
  return out;
}

export function loadCustomModels(): CustomModels {
  if (typeof localStorage === "undefined") {
    return { ...EMPTY_CUSTOM_MODELS };
  }
  try {
    const raw = localStorage.getItem(CUSTOM_MODELS_STORAGE_KEY);
    if (!raw) return { cloud: [], local: [] };
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return { cloud: [], local: [] };
    const record = parsed as Record<string, unknown>;
    if (record.version !== STORAGE_VERSION) return { cloud: [], local: [] };
    return {
      cloud: parseModelOptions(record.cloud),
      local: parseModelOptions(record.local),
    };
  } catch {
    return { cloud: [], local: [] };
  }
}

export function saveCustomModels(models: CustomModels): void {
  if (typeof localStorage === "undefined") {
    return;
  }
  try {
    const body: StoredCustomModelsPayload = {
      version: STORAGE_VERSION,
      cloud: models.cloud,
      local: models.local,
    };
    localStorage.setItem(CUSTOM_MODELS_STORAGE_KEY, JSON.stringify(body));
  } catch (e) {
    if (typeof console !== "undefined" && console.warn) {
      console.warn("[customModels] save failed", e);
    }
  }
}

export function addCustomModel(
  models: CustomModels,
  source: ModelSource,
  option: ModelOption
): CustomModels {
  const current = models[source].filter((m) => m.id !== option.id);
  return { ...models, [source]: [...current, option] };
}

export function removeCustomModel(
  models: CustomModels,
  source: ModelSource,
  id: string
): CustomModels {
  return { ...models, [source]: models[source].filter((m) => m.id !== id) };
}

function mergeList(serverList: ModelOption[], customList: ModelOption[]): ModelOption[] {
  const seen = new Set(serverList.map((m) => m.id));
  return [...serverList, ...customList.filter((m) => !seen.has(m.id))];
}

/** 把自定义模型并入后端返回的配置，服务端条目优先。 */
export function mergeCustomModels(
  config: ConfigResponse,
  models: CustomModels
): ConfigResponse {
  return {
    ...config,
    openRouterModels: mergeList(config.openRouterModels, models.cloud),
    ollamaModels: mergeList(config.ollamaModels, models.local),
  };
}
