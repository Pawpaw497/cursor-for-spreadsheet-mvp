# Agent 演进与可观测性

## 日志与 Agent 排障

Agent 相关请求与 **`traceId`**（请求头 `X-Request-ID`）对齐方式与普通 Plan 一致。

### 后端埋点

- **路由**：[`server/app/api/routes/agent.py`](server/app/api/routes/agent.py) — `/api/agent`、`/api/agent-stream` 入口摘要（表数量、历史轮数、`max_turns`、模型来源等）。
- **编排与循环**：[`server/app/agent/orchestrator.py`](server/app/agent/orchestrator.py) — LangGraph `StateGraph`：`context_analyzer` → `intent_analyzer` → `llm_decide` ↔ `tool_exec`；同步 `POST /api/agent` 经 `run_agent_orchestrated`（`run_agent_loop` 为其别名）；SSE 经 `stream_agent_events`（事件名与字段不变： `tool_call` / `tool_result` / `plan_done` / `finish` / `clarification`）。
- **单步决策**：[`server/app/agent/decision.py`](server/app/agent/decision.py) — JSON 解析/Plan 校验失败日志；`decision` 每步的终止/继续语义（`output_plan` / `call_tool` / `finish` / `ask_clarification`；`current_turn` 达 `max_turns` 时 `finish: max_turns`）。
- **工具**：[`server/app/services/tools.py`](server/app/services/tools.py) — `run_tool` 工具名、参数键名、结果长度；未知工具 / 参数错误 / 异常。
- **LLM**：[`server/app/services/llm.py`](server/app/services/llm.py) — `call_llm` / `call_llm_with_tools` 耗时与消息规模摘要。

### 前端

当前主界面以 **Plan 直连** 为主；若后续接入 `/api/agent` 流式 UI，建议在对应 `fetch`/SSE 封装中复用 [`client/src/llm.ts`](client/src/llm.ts) 的 `X-Request-ID` 与 `request_*` 日志模式，并在 UI 层补充 `agent_*` 事件名。

### 建议排查顺序

1. 用 **`traceId`** 对齐 `spreadsheet.http` 的 `request start/end`。
2. 查看 `spreadsheet.api.agent` 与 `spreadsheet.agent.decision` 的终端/结束原因。
3. 若涉及工具调用，过滤 `spreadsheet.services.tools` 的 `run_tool` 行。
4. 若浏览器控制台出现 **`plan_request_error`** 且含 **`technicalDetail`**，多为 OpenRouter 鉴权问题：核对 `OPENROUTER_API_KEY` 与 OpenRouter 控制台 Key 是否有效。

## Plan 与提示词

- **列统计注入**：单表/多表 user 消息在 [`server/app/services/prompts.py`](server/app/services/prompts.py) 中追加 [`build_column_stats_text`](server/app/services/prompt_content.py) 输出（空值数、去重数、数值 min/max/mean 等，基于**样本行**），与仅依赖 schema+样本相比更有利于减少盲目 transform。
- **校验 Step**：`validate_table` 在引擎中不修改行数据，向 Diff 的 `validationWarnings` / `validationErrors` 写入说明；与 Agent 侧 `get_column_stats` 等工具为互补关系（Plan 无 tools 时也能做列级检查）。
- **系统 Rules**：[`server/app/services/prompt_content.py`](server/app/services/prompt_content.py) 的 `_SPREADSHEET_RULES` / `_PROJECT_RULES` 覆盖主要 step 的选用说明，随 Pydantic `Plan` 的 JSON Schema 注入 system prompt。

## Load API 模型

- [`server/app/api/routes/load.py`](server/app/api/routes/load.py) 中 `LoadedTable` 使用 Python 字段 `table_schema`，通过 `validation_alias` / `serialization_alias` 保持 JSON 键仍为 `schema`，消除与 `BaseModel` 的字段遮蔽 `UserWarning`。
