# 功能亮点（当前能力）

> **维护约定**：每次功能修改后，在本文档与 `AGENT_IMPROVEMENTS.md` 中同步更新（不要求在聊天中展开细节）。

---

## 产品形态

- **Cmd+K 式表格编辑**：自然语言 + 当前表上下文 → 结构化执行计划 → Diff 预览 → 一键 Apply。
- 支持**单表**与**多表/项目**（join、create_table）。

---

## 已实现能力

### 交互与计划

- **单表计划**（`/api/plan`）：前后端共享统一的 PlanStep 语义，支持：
  - 列级操作：`add_column`、`transform_column`（trim/lower/upper/replace/parse_date）、`rename_column`、`delete_column`、`reorder_columns`、`cast_column_type` 等；
  - 行级操作：`filter_rows`、`delete_rows`、`deduplicate_rows`、`sort_table`、`fill_missing`（constant/mean/median/mode）等；
  - add_column 的 expression 支持 LLM 返回完整箭头形式（`row => body`），前后端执行前统一 strip 为 body，Apply 后新列可正确填充。
- **多表计划**（`/api/plan-project`）：在单表能力基础上，支持 `join_tables`、`create_table`、`aggregate_table`、`union_tables`、`lookup_column` 等多表操作，统一由前端与后端的执行引擎理解与落地。
- **Agent 模式**（`/api/agent`）：多轮推理 + 工具调用，同一请求体（多表格式），返回 plan 或 error/clarification。
- **Diff 预览 + 执行路径 + 撤销**：
  - Diff 高亮分为两层：
    - 表格内：新增列使用浅绿色列头和单元格背景（`cell-added` / `col-header-added`），修改列使用浅黄色背景（`cell-modified` / `col-header-modified`），用户可以直接在主工作表中看到本次操作影响范围。
    - AI 面板中：以 JSON 形式展示 `Diff`（`addedColumns` / `modifiedColumns`），默认只显示前几行，支持「展开全部 Diff / 收起 Diff」按钮；若计划包含新建表，还会额外列出将要创建的表名。
  - 执行路径：
    - 单表场景可由前端先本地预览 Diff，再通过 `/api/execute-plan` 在后端一次性执行 Plan 并返回最新表状态；
    - 多表 / 项目场景可通过 `/api/projects/{id}/execute-plan` 基于 ProjectState 执行 Plan，并写回后端的项目内表集合。
  - 撤销：每次 Apply 前前端都会保存一次快照，工具栏的「撤销」按钮会将表格恢复到最近一次 Apply 前的状态（当前为前端内存级别，不做持久化版本管理）。

### 对话视图与历史

- **Chat 气泡视图**：右侧 AI 面板的 Chat 标签页展示自然语言对话：
  - 由 `/api/chat-history` 聚合最近一次或多次会话中的消息，并与当前会话生成的即时消息（live）一起展示；
  - 区分 `user` / `assistant` / `system` 三种角色：用户消息右对齐深色气泡，模型回复左对齐浅灰色气泡，系统提示为黄色卡片；
  - 通过 `source` 字段区分历史消息与现场消息，历史消息额外显示「历史」标签，并统一展示时间戳。
- **技术历史视图（History 标签）**：
  - 使用 `conversations` 列表记录每一次对 LLM 的调用，包括 prompt、请求 payload、Plan JSON、Diff、模型来源（云端/本地）与模型 ID 等；
  - 支持按条目展开/收起「发送给 AI 的内容」和「AI 回复」，同时提供 Diff 的截断/展开按钮，方便在调试时快速复现具体请求与响应。

### Agent 骨架与工具

- **AgentState**（`app/agent/state.py`）：显式状态（tables、messages、applied_plans_summary、conversation、current_turn、max_turns）。
- **动作枚举**（`app/agent/actions.py`）：call_tool / output_plan / ask_clarification / finish，与 payload。
- **decision + run_agent_loop**（`app/agent/decision.py`）：单步决策与循环，支持 use_tools，并内置基础澄清逻辑（多表未指定 table 时 ask_clarification）。
- **工具集**（`app/services/tools.py`）：get_schema、get_sample_rows、get_column_stats、validate_expression；LLM 通过 tool calling 调用。
- **LLM tool calling**（`app/services/llm.py`）：call_llm_with_tools（OpenRouter + Ollama），返回 content 或 tool_calls。

### 后端结构

- FastAPI：`/api/plan`、`/api/plan-project`、`/api/agent`、`/api/agent-stream`、`/api/export-excel`、`/api/config`、health。
- 模型：OpenRouter（云端）、Ollama（本地），可配置模型列表。

### 数据加载与导入体验

- 启动时示例加载 `/api/load-sample` 采用有限重试 + 缩短单次超时策略，在后端不可用时总等待时间控制在约 8–10 秒内，并给出明确错误提示。
- 导入 Excel/CSV 文件 `/api/import-file` 在前端使用更长超时窗口（约 20 秒）和更清晰的状态文案，避免大文件导入时用户误以为前端卡死。
- 后端对 Excel/CSV 解析增加耗时与行列统计日志，并在解析函数中预留「最大行数」参数，便于未来支持仅加载前 N 行预览的大文件场景。

---

## 非目标（当前范围）

- 协同编辑、完整公式引擎、多表血缘图、外部数据源连接。

