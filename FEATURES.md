# 功能亮点（当前能力）

> **维护约定**：每次功能修改后，在本文档与 `AGENT_IMPROVEMENTS.md` 中同步更新（不要求在聊天中展开细节）。

---

## 产品形态

- **Cmd+K 式表格编辑**：自然语言 + 当前表上下文 → 结构化执行计划 → Diff 预览 → 一键 Apply。
- 支持**单表**与**多表/项目**（join、create_table）。

---

## 已实现能力

### 交互与计划

- **单表计划**（`/api/plan`）：add_column、transform_column（trim/lower/upper/replace/parse_date）。
- **多表计划**（`/api/plan-project`）：上述 + join_tables、create_table。
- **Agent 模式**（`/api/agent`）：多轮推理 + 工具调用，同一请求体（多表格式），返回 plan 或 error/clarification。
- Diff 预览、一键 Apply、撤销（基于快照）。

### Agent 骨架与工具

- **AgentState**（`app/agent/state.py`）：显式状态（tables、messages、applied_plans_summary、conversation、current_turn、max_turns）。
- **动作枚举**（`app/agent/actions.py`）：call_tool / output_plan / ask_clarification / finish，与 payload。
- **decision + run_agent_loop**（`app/agent/decision.py`）：单步决策与循环，支持 use_tools，并内置基础澄清逻辑（多表未指定 table 时 ask_clarification）。
- **工具集**（`app/services/tools.py`）：get_schema、get_sample_rows、get_column_stats、validate_expression；LLM 通过 tool calling 调用。
- **LLM tool calling**（`app/services/llm.py`）：call_llm_with_tools（OpenRouter + Ollama），返回 content 或 tool_calls。

### 后端结构

- FastAPI：`/api/plan`、`/api/plan-project`、`/api/agent`、`/api/agent-stream`、`/api/export-excel`、`/api/config`、health。
- 模型：OpenRouter（云端）、Ollama（本地），可配置模型列表。

---

## 非目标（当前范围）

- 协同编辑、完整公式引擎、多表血缘图、外部数据源连接。
