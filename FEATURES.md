# 功能与能力清单（MVP）

本文档与 [README.md](README.md) 配合使用，侧重**可观测性 / 日志**与能力速查。

## 控制台日志（前端）

- **模块**：[`client/src/logger.ts`](client/src/logger.ts) — `logInfo` / `logError` / `logDebug`，统一 `[APP]` 输出。
- **会话**：`sessionId`（`getSessionId()`），页面打开时生成。
- **请求关联**：`generateTraceId()`；HTTP 层在 [`client/src/llm.ts`](client/src/llm.ts) 的 `fetchWithTimeout` 中写入 `X-Request-ID`，并打 `request_start` / `request_success` / `request_error`。
- **UI 事件**：[`client/src/App.tsx`](client/src/App.tsx) — 加载示例、Cmd+K、生成 Plan、Diff 预览、Apply、导入/导出、撤销、面板折叠等。

## 控制台日志（后端）

- **配置**：[`server/app/logging_config.py`](server/app/logging_config.py) — `init_logging()`、`get_logger("spreadsheet.<module>")`、`trace_id` 上下文。
- **HTTP**：[`server/app/main.py`](server/app/main.py) — `RequestLoggingMiddleware`、全局异常处理、`X-Request-ID` 回传与 `expose_headers`。
- **业务**：Plan / Agent / LLM / 工具 / 项目存储 / 导入导出等路由与服务层见各文件内 `get_logger` 调用。

## 环境变量速查

| 变量 | 作用 |
|------|------|
| `LOG_LEVEL` | 后端日志级别，默认 `INFO`。 |
| `LOG_FULL_TRACEBACK` | 未捕获异常是否打印完整栈，默认开启。 |
| `VITE_ENABLE_CONSOLE_LOG` | 前端开发态是否输出 `logInfo`/`logDebug`；`0` 关闭。 |
| `OPENROUTER_API_KEY` | 云端模型（OpenRouter）鉴权；缺失或无效时 Plan 接口返回 502，前端状态栏提示鉴权失败。 |

## OpenRouter 错误与排障

- **后端**：[`server/app/services/llm.py`](server/app/services/llm.py) 在 HTTP 401/403 时将错误规范为带 `AUTH_ERROR:` 前缀的 `RuntimeError`；[`server/app/api/routes/plan.py`](server/app/api/routes/plan.py) 将其映射为可读中文 `detail`（仍附「技术详情」长尾供排查）。
- **前端**：[`client/src/llm.ts`](client/src/llm.ts) 的 `splitApiErrorDetail` 用于从 `detail` 中拆分技术尾段；[`client/src/App.tsx`](client/src/App.tsx) 在生成 Plan 失败时把 `technicalDetail` 写入 `plan_request_error` 日志，状态栏展示简短中文说明。

## Plan Step 与 Diff

- **Step 类型**：见 [`server/app/models/plan.py`](server/app/models/plan.py) 的 `Step` Union；含数据变换、多表 join/union/lookup、`delete_column` / `reorder_columns`、**`validate_table`（不修改数据）**、**`pivot_table` / `unpivot_table`（新表）** 等。
- **执行引擎**：前后端 [`client/src/engine.ts`](client/src/engine.ts) 与 [`server/app/services/plan_executor.py`](server/app/services/plan_executor.py) 语义对齐。
- **Diff**：`addedColumns` / `modifiedColumns` 与 **validationWarnings** / **validationErrors**（`validate_table` 步骤写入，便于预览告警）。

## Agent 与 LangGraph 编排

- **同步**：`POST /api/agent` 在 [`server/app/api/routes/agent.py`](server/app/api/routes/agent.py) 中调用 `run_agent_orchestrated`；与 Plan 多表请求体同型，返回 `Plan` 或澄清/错误结构不变。
- **流式（SSE）**：`POST /api/agent-stream` 经 [`server/app/agent/orchestrator.py`](server/app/agent/orchestrator.py) 的 `stream_agent_events`，事件名 `plan_done` / `finish` / `clarification` / `tool_call` / `tool_result` 与字段与迁移前一致。
- **子代理（MVP）**：`context_analyzer` / `intent_analyzer` 在 [`server/app/agent/sub_agents/`](server/app/agent/sub_agents/) 中当前为透传，主推理仍在 `decision`；图为 ReAct 形式（`llm_decide` 与 `tool_exec` 条件边回环至 `llm_decide`）。

## 非目标

- 不落盘集中式日志系统（ELK 等）；当前仅为 STDOUT + 浏览器 Console。
- 不在日志中输出完整表数据或完整 prompt，仅长度与统计。
