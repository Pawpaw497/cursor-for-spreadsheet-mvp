# Cursor plans in this workspace

**Canonical path:** [`.cursor/plans/`](../../.cursor/plans/) at the repository root. Edit these files in the workspace; they are not resolved from `~/.cursor/plans/`. Merged / deduplicated plans (see below) live here.

**Last backlog refresh:** 2026-04-22 — 已完成或已取消的计划文件已从本目录移除，仅保留仍待排期/参考用的 `.plan.md`；P1 主路径项已在仓库落地。

Selection criteria (keyword / path match for this spreadsheet AI demo):

- Filename or body mentions: `spreadsheet`, `cursor-spreadsheet`, Cmd+K, `engine.ts`, `plan.py`, `plan_executor`, AG Grid, LangGraph / agent migration targeting the `spreadsheet-cursor-mvp` tree.
- Excluded as unrelated: `open-xrd-*`, `update-project-description` (XRD).

## Merged plans (replace older duplicates)

| Canonical file | Replaced（旧稿已删除，无链接） |
| ---------------- | -------- |
| [langgraph-pydantic-ai-migration.plan.md](../../.cursor/plans/langgraph-pydantic-ai-migration.plan.md) | `langgraph+pydantic_ai_迁移_0c573c3e.plan.md`, `langgraph_+_pydantic_ai_迁移_8ebc8333.plan.md` |
| [langgraph-three-subagents-migration.plan.md](../../.cursor/plans/langgraph-three-subagents-migration.plan.md) | `langgraph_agent_平移方案_b97aa0b1.plan.md`, `langgraph三子代理迁移_e37ae97f.plan.md` |
| [spreadsheet-plan-step-types-reference.plan.md](../../.cursor/plans/spreadsheet-plan-step-types-reference.plan.md) | `spreadsheet-plan-operation-types_95350f32.plan.md`, `spreadsheet-supported-ops_15df3a60.plan.md` |

> **Note:** 原 `ollama-local-llm-runtime.plan.md`（及合并前的 Ollama 超时类旧稿）已从工作区移除；本地/云端 LLM 配置与排障以 [README](../../README.md)、[FEATURES](../../FEATURES.md) 为准。

## 仍需完成（按优先级）

> 仅包含 **`pending`** 且仍值得在仓库内推进的条目。实施顺序：**P2 → P3**（原 P1 已完成）。

**待办真源：** 下列每条在对应 `.plan.md` 的 **YAML frontmatter `todos`**（`id` / `content` / `status`）中维护；在 Cursor 计划视图可勾选追踪。正文「执行清单」仅作人类可读对照，改进度以 YAML 为准（与 [plan-explicit-todos.mdc](../../.cursor/rules/plan-explicit-todos.mdc) 一致）。

### P1 — （暂无）

当前无阻塞主路径的低成本 backlog 项。

### P2 — 能力或架构，按需立项

#### [agent-step-enhancement_95f11d59.plan.md](../../.cursor/plans/agent-step-enhancement_95f11d59.plan.md)

**YAML `todos`（`pending`）——以 plan 文件为准：**

| `id` | 摘要（见 plan 内 `content`） |
|------|------------------------------|
| `p1-validate-table` | P1：`validate_table` + Diff 扩展 |
| `p1-pivot-table` | P1：`pivot_table` 前后端 |
| `p1-unpivot-table` | P1：`unpivot_table` 前后端 |
| `p2-prompt-enhance` | P2：`prompt_content.py` Rules + 可选列统计注入 |

**理由：** 增强 Plan 表达力与提示词；非阻塞当前最小闭环，有产品诉求时逐项做。

#### [langgraph-three-subagents-migration.plan.md](../../.cursor/plans/langgraph-three-subagents-migration.plan.md)

**YAML `todos`（`pending`）——以 plan 文件为准：**

| `id` | 摘要（见 plan 内 `content`） |
|------|------------------------------|
| `unify-imports-state` | 统一 AgentState/Action 与单一命名空间 |
| `build-langgraph-orchestrator` | LangGraph 编排 + ReAct + 可选前置节点 |
| `implement-subagents-mvp` | 子代理 MVP（plan_generator 承载现有逻辑） |
| `switch-agent-routes` | `/api/agent` 与 `/api/agent-stream` 切到新编排器 |
| `regression-and-docs` | 回归 + [FEATURES.md](../../FEATURES.md) / [AGENT_IMPROVEMENTS.md](../../AGENT_IMPROVEMENTS.md) |

**理由：** 较大重构；仅在决定统一 Agent 编排、消除历史包名/状态漂移时启动（与 [agent-build-practices.mdc](../../.cursor/rules/agent-build-practices.mdc) 中 LangGraph 方向一致）。

### P3 — 路线图池（不整单「完成」）

#### [spreadsheet-cursor-roadmap_66f6c3b6.plan.md](../../.cursor/plans/spreadsheet-cursor-roadmap_66f6c3b6.plan.md)

**YAML `todos`（`pending`）——以 plan 文件为准：**

| `id` | 摘要（见 plan 内 `content`） |
|------|------------------------------|
| `enhance-ux` | 多步 Plan 可视化、Step 级控制、Diff/撤销 |
| `agent-stream-ui` | 前端 `/api/agent-stream`、流式与澄清 |
| `agent-tools-and-refinement` | Agent tools + Plan refinement |
| `testing-and-observability` | 测试覆盖、结构化日志/可观测性 |
| `config-and-docs` | 配置、架构/使用文档、协作流程 |

**理由：** 合集型愿景；其中**可观测性**已由日志方案部分覆盖。后续应**拆成独立 issue/小 plan** 再排期，而不是把本文件当作闭集交付。

---

## 已从工作区移除的计划（本迭代不交付 / 已完成）

以下 `.plan.md` 已删除；内容如需追溯可使用 git 历史。含：日志设计、Plan 执行迁移、导入 Excel/CSV、OpenRouter 401 体验、Ollama 运行时、演示叙事、操作分类、plan types、聊天历史视图、扩展 step types、引擎重复代码、sort 校验、git 冲突、网格 Diff、导入优化、Python 环境文档、简历重写、导入样本修复、README/FEATURES 更新等（均为 INDEX 原表中的 **completed** / **cancelled** / 已全部 completed 项）。

---

## Completion summary（当前目录内文件）

| File | Plan name (YAML) | Notes |
|------|------------------|-------|
| [agent-step-enhancement_95f11d59.plan.md](../../.cursor/plans/agent-step-enhancement_95f11d59.plan.md) | agent-step-enhancement | **pending** ×4（见 P2） |
| [langgraph-three-subagents-migration.plan.md](../../.cursor/plans/langgraph-three-subagents-migration.plan.md) | LangGraph three-subagent migration | **pending** ×5（见 P2） |
| [spreadsheet-cursor-roadmap_66f6c3b6.plan.md](../../.cursor/plans/spreadsheet-cursor-roadmap_66f6c3b6.plan.md) | spreadsheet-cursor-roadmap | **pending** ×5（见 P3 池化） |
| [langgraph-pydantic-ai-migration.plan.md](../../.cursor/plans/langgraph-pydantic-ai-migration.plan.md) | LangGraph + Pydantic AI migration | `todos: []` 蓝图，**未排期** |
| [apply_后新列为空根因与修复_bd9d85c4.plan.md](../../.cursor/plans/apply_后新列为空根因与修复_bd9d85c4.plan.md) | Apply 后新列为空根因与修复 | `todos: []` |
| [spreadsheet-plan-step-types-reference.plan.md](../../.cursor/plans/spreadsheet-plan-step-types-reference.plan.md) | Plan step types reference | `todos: []` |
| [spreadsheet-mvp-interview-enhancements_a32a1185.plan.md](../../.cursor/plans/spreadsheet-mvp-interview-enhancements_a32a1185.plan.md) | spreadsheet-mvp-interview-enhancements | `todos: []` |

## 无 YAML 待办的设计/说明稿

- [langgraph-pydantic-ai-migration.plan.md](../../.cursor/plans/langgraph-pydantic-ai-migration.plan.md) — 远期 LangGraph + Pydantic AI 蓝图（**仅在有重构立项时**启用）  
- [apply_后新列为空根因与修复_bd9d85c4.plan.md](../../.cursor/plans/apply_后新列为空根因与修复_bd9d85c4.plan.md) — 根因与修复说明  
- [spreadsheet-plan-step-types-reference.plan.md](../../.cursor/plans/spreadsheet-plan-step-types-reference.plan.md) — Step 类型参考  
- [spreadsheet-mvp-interview-enhancements_a32a1185.plan.md](../../.cursor/plans/spreadsheet-mvp-interview-enhancements_a32a1185.plan.md) — 面试/表达向  

路径说明：计划 YAML 的 `name` 字段（如 `spreadsheet-cursor-roadmap`）为 Cursor 计划标识。本表链接均相对 **仓库根**（`../../…` 自本文件 [`.cursor/plans/INDEX.md`](../../.cursor/plans/INDEX.md) 解析）指向本工作区中的文件。
