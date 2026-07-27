import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigResponse } from "./llm";
import {
  CUSTOM_MODELS_STORAGE_KEY,
  addCustomModel,
  loadCustomModels,
  mergeCustomModels,
  normalizeCustomModelInput,
  removeCustomModel,
  saveCustomModels,
} from "./customModelStorage";

const store = new Map<string, string>();

const sampleConfig: ConfigResponse = {
  openRouterModel: "openrouter/auto",
  openRouterModels: [
    { id: "openrouter/auto", label: "Auto" },
    { id: "openai/gpt-4o-mini", label: "GPT-4o mini" },
  ],
  ollamaModel: "qwen2.5:7b",
  ollamaModels: [{ id: "qwen2.5:7b", label: "qwen2.5:7b" }],
};

beforeEach(() => {
  store.clear();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v);
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("normalizeCustomModelInput", () => {
  it("trims id and falls back to id as label", () => {
    expect(normalizeCustomModelInput("  anthropic/claude-opus-4  ", "  ")).toEqual({
      id: "anthropic/claude-opus-4",
      label: "anthropic/claude-opus-4",
    });
  });

  it("keeps an explicit label", () => {
    expect(normalizeCustomModelInput("x/y", "  My Model ")).toEqual({
      id: "x/y",
      label: "My Model",
    });
  });

  it("rejects empty or whitespace-only ids", () => {
    expect(normalizeCustomModelInput("", "L")).toBeNull();
    expect(normalizeCustomModelInput("   ", "L")).toBeNull();
  });

  it("rejects ids with whitespace inside", () => {
    expect(normalizeCustomModelInput("foo bar", "")).toBeNull();
  });
});

describe("loadCustomModels", () => {
  it("returns empty lists when nothing is stored", () => {
    expect(loadCustomModels()).toEqual({ cloud: [], local: [] });
  });

  it("round-trips saved models", () => {
    saveCustomModels({
      cloud: [{ id: "a/b", label: "AB" }],
      local: [{ id: "llama3", label: "llama3" }],
    });
    expect(loadCustomModels()).toEqual({
      cloud: [{ id: "a/b", label: "AB" }],
      local: [{ id: "llama3", label: "llama3" }],
    });
  });

  it("ignores payloads with an unknown version", () => {
    localStorage.setItem(
      CUSTOM_MODELS_STORAGE_KEY,
      JSON.stringify({ version: 99, cloud: [{ id: "a/b", label: "AB" }], local: [] })
    );
    expect(loadCustomModels()).toEqual({ cloud: [], local: [] });
  });

  it("ignores malformed json and malformed entries", () => {
    localStorage.setItem(CUSTOM_MODELS_STORAGE_KEY, "{not json");
    expect(loadCustomModels()).toEqual({ cloud: [], local: [] });

    saveCustomModels({ cloud: [], local: [] });
    localStorage.setItem(
      CUSTOM_MODELS_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        cloud: [{ id: "ok/1", label: "OK" }, { id: 42 }, null, { label: "no id" }],
        local: "nope",
      })
    );
    expect(loadCustomModels()).toEqual({ cloud: [{ id: "ok/1", label: "OK" }], local: [] });
  });
});

describe("addCustomModel / removeCustomModel", () => {
  it("appends to the requested source only", () => {
    const next = addCustomModel({ cloud: [], local: [] }, "cloud", {
      id: "a/b",
      label: "AB",
    });
    expect(next).toEqual({ cloud: [{ id: "a/b", label: "AB" }], local: [] });
  });

  it("replaces an existing entry with the same id instead of duplicating", () => {
    const first = addCustomModel({ cloud: [], local: [] }, "cloud", { id: "a/b", label: "AB" });
    const next = addCustomModel(first, "cloud", { id: "a/b", label: "New label" });
    expect(next.cloud).toEqual([{ id: "a/b", label: "New label" }]);
  });

  it("removes by id and source", () => {
    const state = {
      cloud: [{ id: "a/b", label: "AB" }, { id: "c/d", label: "CD" }],
      local: [{ id: "a/b", label: "AB" }],
    };
    const next = removeCustomModel(state, "cloud", "a/b");
    expect(next.cloud).toEqual([{ id: "c/d", label: "CD" }]);
    expect(next.local).toEqual([{ id: "a/b", label: "AB" }]);
  });
});

describe("mergeCustomModels", () => {
  it("appends custom models after the server-provided ones", () => {
    const merged = mergeCustomModels(sampleConfig, {
      cloud: [{ id: "anthropic/claude-opus-4", label: "Opus 4" }],
      local: [{ id: "llama3", label: "llama3" }],
    });
    expect(merged.openRouterModels.map((m) => m.id)).toEqual([
      "openrouter/auto",
      "openai/gpt-4o-mini",
      "anthropic/claude-opus-4",
    ]);
    expect(merged.ollamaModels.map((m) => m.id)).toEqual(["qwen2.5:7b", "llama3"]);
  });

  it("lets a server model win over a custom entry with the same id", () => {
    const merged = mergeCustomModels(sampleConfig, {
      cloud: [{ id: "openrouter/auto", label: "Mine" }],
      local: [],
    });
    expect(merged.openRouterModels).toEqual(sampleConfig.openRouterModels);
  });

  it("keeps the rest of the config untouched", () => {
    const merged = mergeCustomModels(sampleConfig, { cloud: [], local: [] });
    expect(merged.openRouterModel).toBe(sampleConfig.openRouterModel);
    expect(merged.ollamaModel).toBe(sampleConfig.ollamaModel);
  });
});
