import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigResponse } from "./llm";
import {
  MODEL_PREF_STORAGE_KEY,
  loadModelPreference,
  resolveModelPreference,
  saveModelPreference,
} from "./modelPreferenceStorage";

const store = new Map<string, string>();

const sampleConfig: ConfigResponse = {
  openRouterModel: "deepseek/deepseek-v4-pro",
  openRouterModels: [
    { id: "deepseek/deepseek-v4-pro", label: "DeepSeek: DeepSeek V4 Pro" },
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

describe("modelPreferenceStorage", () => {
  it("round-trips save and load", () => {
    saveModelPreference({
      modelSource: "cloud",
      cloudModelId: "openai/gpt-4o-mini",
      localModelId: "qwen2.5:7b",
    });
    expect(loadModelPreference()).toEqual({
      modelSource: "cloud",
      cloudModelId: "openai/gpt-4o-mini",
      localModelId: "qwen2.5:7b",
    });
  });

  it("returns null for corrupt JSON", () => {
    store.set(MODEL_PREF_STORAGE_KEY, "{not json");
    expect(loadModelPreference()).toBeNull();
  });

  it("returns null for invalid modelSource", () => {
    store.set(
      MODEL_PREF_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        modelSource: "edge",
        cloudModelId: "deepseek/deepseek-v4-pro",
        localModelId: "qwen2.5:7b",
      })
    );
    expect(loadModelPreference()).toBeNull();
  });

  it("restores saved IDs when still in config lists", () => {
    const resolved = resolveModelPreference(
      {
        modelSource: "local",
        cloudModelId: "openai/gpt-4o-mini",
        localModelId: "qwen2.5:7b",
      },
      sampleConfig
    );
    expect(resolved).toEqual({
      modelSource: "local",
      cloudModelId: "openai/gpt-4o-mini",
      localModelId: "qwen2.5:7b",
    });
  });

  it("falls back to server defaults when saved ID absent from list", () => {
    const resolved = resolveModelPreference(
      {
        modelSource: "cloud",
        cloudModelId: "removed/model",
        localModelId: "removed:tag",
      },
      sampleConfig
    );
    expect(resolved).toEqual({
      modelSource: "cloud",
      cloudModelId: "deepseek/deepseek-v4-pro",
      localModelId: "qwen2.5:7b",
    });
  });

  it("defaults modelSource to cloud when saved is null", () => {
    const resolved = resolveModelPreference(null, sampleConfig);
    expect(resolved.modelSource).toBe("cloud");
    expect(resolved.cloudModelId).toBe("deepseek/deepseek-v4-pro");
    expect(resolved.localModelId).toBe("qwen2.5:7b");
  });
});
