# 将项目改得更像「Agent」的修改点

本文档基于当前代码结构，列出若希望产品从「单次 LLM 生成计划」升级为更典型的 **Agent** 行为时，可考虑的修改方向与落点。每条会说明：现状、目标、建议改动位置与优先级。

---

## 一、当前形态简述

- **流程**：用户 Cmd+K 输入自然语言 → 后端一次调用 LLM → 返回一个 JSON 计划（`intent` + `steps[]`）→ 前端 Diff 预览 → 用户一键 Apply。
- **特点**：单轮、无工具调用、无流式、无跨请求记忆；LLM 只做「根据上下文直接输出整份计划」。

---

## 二、修改点总览

| 维度           | 现状                     | 目标（更 Agent 化）                     | 优先级建议 |
|----------------|--------------------------|----------------------------------------|------------|
| **实现骨架**   | ✅ **已实现**（见第三节 3.5） | **AgentState + 动作枚举 + decision + run_agent_loop** | **高（已落地）** |
| 执行模式       | 一次生成整份计划         | 多步推理 / 工具调用 / 分步执行与观察   | 高         |
| 流式与可观测   | 基础 SSE 已有（`/api/agent-stream`） | 流式输出 + 推理/工具步骤可观测（前端接入中） | 高         |
| 对话与记忆     | Agent 支持 history / appliedPlansSummary，但前端暂未全面利用 | 多轮对话 + 会话/项目级记忆             | 中         |
| 澄清与确认     | 已有简单 ask_clarification 触发逻辑   | 更丰富的澄清策略与前端交互             | 中         |
| 执行与回滚     | 前端一次性 Apply         | 分步执行、校验、失败可回滚或重试       | 中         |
| 工具与能力     | 无工具，仅靠 prompt     | 读表/统计/校验等工具供 LLM 调用        | 中         |
| 计划迭代       | 仅 JSON 解析失败重试     | 基于执行结果或校验结果 refinement 循环 | 低         |

下面按「实现骨架」→「执行模式」「流式与可观测」「对话与记忆」等分节展开。

---

## 三、实现骨架（优先落地）：AgentState / 动作枚举 / decision 函数

在动手实现「执行模式」「流式」「记忆」等能力前，建议先落地三个**实现骨架**，使 Agent 循环有清晰的数据结构与单步语义。它们与本文档后续各节的对应关系如下。

### 3.1 与 GOAL.md 的对应

- **Phase 3** 要求：MVP、**Agentic Feature**、**agent / tool 调度是否合理**、**人机协作工作流**（而非「更高级的 autocomplete」）。
- 下面三项正是「可调度的、有状态的 agent」的实现基础，与 GOAL 的评价标准一致。

### 3.2 三项骨架与本文档的交叉

| 骨架 | 在本文档中的对应 | 说明 |
|------|------------------|------|
| **AgentState（dict / dataclass）** | 第三节「Agent 循环」、第五节「对话与记忆」 | 文档中的「每轮：LLM 返回 → 执行 tool → 结果塞回 messages → 再调 LLM」隐含了「有一块状态每轮更新」。**AgentState** 把这块状态具象化：当前表数据、messages、已执行步骤、会话/已应用计划摘要等。多轮记忆、流式推送的「当前状态」都基于它。 |
| **动作枚举（非常关键）** | 第三节「工具 vs 最终计划」、第六节「澄清」 | 文档写了「LLM 返回 either 推理+tool_calls 或 最终 plan」以及「输出 clarification」。**动作枚举**即「下一步做什么」的离散取值，例如：`call_tool` / `output_plan` / `ask_clarification` / `finish`。工具调用、澄清、输出计划都对应枚举的一种；没有枚举则用 if/字符串解析，难以扩展。 |
| **decision 函数（agent 的心脏）** | 第三节「Agent 循环的单步」 | 文档中的「每轮：LLM 返回 → 解析 → 若 tool 则执行并再调 LLM」就是**单步决策**。**decision**：输入当前 **AgentState**，输出一个 **动作（枚举）**（并可能更新 state）。实现上多为：组 messages → 调 LLM → 解析响应（tool_call / plan / clarification）→ 返回对应动作。循环即：反复 `state → decision(state) → 根据 action 更新 state 或 break`。 |

**结论**：三项与文档无冲突，是同一套设计的不同抽象层级——文档偏产品/流程，三项偏实现骨架；先落地骨架，再在骨架上挂流式、记忆、澄清等。

### 3.3 统一优先级与建议实现顺序

| 维度 | 本文档原优先级 | 骨架视角 | 综合 |
|------|----------------|----------|------|
| AgentState | （隐含在「循环」中） | 第一项，建议先定 | **高**。先定状态结构，循环、流式、记忆都依赖它。 |
| 动作枚举 | 分散在工具/澄清/计划 | 「非常关键」 | **高**。决策与 SSE 事件类型都可与枚举对齐，避免 ad-hoc 解析。 |
| decision 函数 | 隐含在「Agent 循环」 | 「agent 的心脏」 | **高**。即循环的单步：state → (LLM+解析) → action。 |

**建议实现顺序**：**AgentState → 动作枚举 → decision 函数**，再用「循环：state → decision → 按 action 更新 state」把「执行模式」跑通，最后在同一 state/action 上挂流式（每步推送 state/action 摘要）、记忆（state 中加 conversation/applied_plans）、澄清（枚举中加 `ask_clarification`）。

### 3.4 建议落点（目录/文件）

- **AgentState**：`server/app/agent/state.py`（或 `app/models/agent_state.py`），dataclass 或 TypedDict，字段如：`tables`, `messages`, `applied_plans_summary`, `current_turn`, `max_turns`。
- **动作枚举**：同文件或 `server/app/agent/actions.py`，如 `AgentAction = Literal["call_tool", "output_plan", "ask_clarification", "finish"]`，并可带 payload（tool_name/args、plan、clarification 等）。
- **decision 函数**：`server/app/agent/decision.py`（或 `services/agent.py`），签名如 `async def decision(state: AgentState) -> tuple[AgentState, AgentAction]`，内部组 messages、调 LLM、解析并返回下一动作与更新后的 state。

### 3.5 已实现情况（与代码同步）

骨架已落地于 **`server/app/agent/`**：

| 项 | 文件 | 说明 |
|----|------|------|
| AgentState / TableContext | `state.py` | dataclass，含 tables、messages、applied_plans_summary、current_turn、max_turns、user_prompt、model 配置；`initial_state_from_plan_request` / `initial_state_from_project_request` 从现有请求构建初始 state。 |
| 动作枚举 | `actions.py` | `AgentActionKind`、`CallToolAction`、`OutputPlanAction`、`AskClarificationAction`、`FinishAction` 及对应 payload；`action_kind(action)` 便于分支与 SSE。 |
| decision | `decision.py` | `decision(state) → (state, action)`：组 messages（首轮/多轮）、调 LLM、解析 JSON → Plan，返回 `OutputPlanAction` 或 `FinishAction`；解析失败重试一次。 |
| Agent 循环 | `decision.py` | `run_agent_loop(initial_state) → (state, action)`：循环调用 decision，遇 `output_plan` / `finish` / `ask_clarification` 即返回；`call_tool` 时用占位 stub 写回 messages 后继续（为接入真实 tools 预留）。 |

路由层尚未切换到 `run_agent_loop`，现有 `/api/plan`、`/api/plan-project` 仍为单次 `call_llm`；后续可新增 `/api/agent` 或将现有路由改为「用 initial_state + run_agent_loop，按 action 返回 plan 或错误」。

---

## 四、执行模式：从「一次出计划」到「多步推理 + 工具」

### 4.1 现状

- `server/app/api/routes/plan.py`：一次 `call_llm`，期望直接得到完整 Plan JSON。
- `server/app/services/prompts.py`：System prompt 要求「只输出 JSON」，无 tool / function calling。

### 4.2 目标

- Agent 可以：**先思考 → 调用工具（读 schema、看样本、校验表达式）→ 根据工具结果再决定下一步 → 最终给出或执行计划**。
- 行为上更接近 ReAct / Tool-use：不是「一次性猜一个完整计划」，而是「多轮：推理 + 调用工具 + 观察 + 再推理」。

### 4.3 建议改动

1. **后端**
   - 在 `server/app/services/llm.py` 中支持 **function calling / tools**（若用 OpenRouter/Ollama，按各自 API 传 `tools` / `tool_choice`）。
   - 新增 `server/app/services/tools.py`（或 `agent/tools/`）：
     - `get_schema(table?)`、`get_sample_rows(table?, n)`、`get_column_stats(table, column)`；
     - `validate_expression(expression, sample_row)`、`dry_run_step(step, table)` 等。
   - 在 `plan.py` 或新路由（如 `/api/agent`）中实现 **Agent 循环**：
     - 每轮：LLM 返回 either「一段推理 + 可选 tool_calls」或「最终 plan」；
     - 若为 tool_calls：在服务端执行对应 tool，把结果塞回 messages，再调 LLM；
     - 直到 LLM 输出「最终计划」或达到最大轮数。

2. **前端**
   - 若保留现有 `/api/plan`，可新增「Agent 模式」入口（如 Cmd+K 里勾选「Agent 模式」），调用新接口并展示多步过程（见下一节）。

3. **优先级**：高。这是从「单次生成」升级为「Agent」的核心变化。

---

## 五、流式输出与可观测性

### 5.1 现状

- `call_ollama` / `call_openrouter` 仍为非流式 token 输出，但已通过 `/api/agent-stream` 提供基于 AgentState 的 **SSE 事件流**。
- SSE 事件类型与动作枚举对齐：`tool_call`、`tool_result`、`plan_done`、`finish`、`clarification`，前端尚未完全接入展示。

### 5.2 目标

- 流式返回：思考内容、工具调用、每步结果、最终计划，用户能实时看到进度。
- 便于调试与信任：知道 Agent 为何做出某个计划。

### 5.3 建议改动

1. **后端**
   - ✅ 已实现 `/api/agent-stream`（SSE）：基于 Agent 循环按步骤推送 `tool_call`、`tool_result`、`plan_done`、`finish`、`clarification` 事件。
   - 如需更细粒度的「reasoning / token 级流式」，可在未来引入真正的流式 LLM 输出（当前暂不必做）。

2. **前端**
   - `client/src/llm.ts`：新增 `requestPlanStream()`（或 `requestAgentStream()`），用 `EventSource` 或 `fetch` + 读 stream，解析 SSE。
   - `client/src/App.tsx`：在 Cmd+K 面板中增加「进行中」区域：
     - 显示当前轮次的推理片段、工具调用与结果、最终 plan 的逐步成型；
     - 可选：折叠/展开「中间步骤」，只保留「最终计划 + Diff」。

3. **优先级**：高。流式 + 可观测是 Agent 体验的关键。

---

## 六、对话与记忆（多轮 + 会话/项目记忆）

### 6.1 现状

- Agent 接口 `/api/agent` / `/api/agent-stream` 已支持在请求体中携带 `history` 与 `appliedPlansSummary`（`AgentProjectPlanRequest`），后端会在 `initial_state_from_agent_project_request` 中拼进 `AgentState.messages` / `conversation`。
- 现阶段前端尚未全面利用这些字段构造真正的多轮对话记忆，仅作为基础能力存在。

### 6.2 目标

- **多轮对话**：用户可以说「再加一列 total」「把刚才那列删掉」「把 email 列改成小写」（指代上一轮的输出）。
- **会话/项目记忆**：简短记录「本会话已执行的计划」或「当前项目里各表做过的主要操作」，供 LLM 理解上下文。

### 6.3 建议改动

1. **后端**
   - ✅ 已在 Agent 路由中使用 `AgentProjectPlanRequest`（包含 `history` 与 `appliedPlansSummary`），并在 `initial_state_from_agent_project_request` 中写入 `AgentState.messages` / `conversation` / `applied_plans_summary`。
   - 后续可考虑在 system 或首条 user 中注入「近期已执行计划」的简短摘要，而不仅仅是保存在 state 内部。

2. **前端**
   - `App.tsx` 中已有 `conversations` 列表；下一步应在调用 `/api/agent` / `/api/agent-stream` 时，将「上一轮的 (userPrompt, plan, diff)」转为 `history`（user/assistant turns），并把已应用计划摘要写入 `appliedPlansSummary`。

3. **持久化（可选）**
   - 若要做「项目级」记忆：可把 session 或 project 的摘要存 DB/文件，在打开项目时加载，并在每次 Apply 后更新。

4. **优先级**：中。多轮与记忆能明显提升「像在跟助手对话」的感觉。

---

## 七、澄清与确认（减少静默猜测）

### 7.1 现状

- Agent 决策中已加入简单的澄清触发逻辑：在多表场景下，如 Plan 中存在未指定 `table` 的 add_column / transform_column，会返回 ask_clarification，而不是直接输出 plan。

### 7.2 目标

- 当存在明显歧义（如多列同名、多表未指定、操作对象不明确）时，Agent 输出「澄清请求」而非直接执行，由前端以对话框或内联选择让用户确认。

### 7.3 建议改动

1. **后端**
   - ✅ 已在 Agent 决策中实现基础版澄清：`_maybe_need_clarification(state, plan)` 会根据多表 + 未指定 table 的步骤生成 `AskClarificationAction`，携带 `question` / `options`（表名列表）/ `context`（歧义步骤说明）。
   - 后续可以扩展更多澄清场景（如多列同名、复杂 join 条件不明确等），并考虑在 LLM Prompt 中显式鼓励先澄清再执行。

2. **前端**
   - 若响应为 `clarification`：展示问题与选项，用户选择后把答案拼进 prompt 再次请求（或作为 follow-up 消息）。

3. **优先级**：中。能减少误操作、提升可控性。

---

## 八、分步执行、校验与回滚

### 8.1 现状

- 前端 `engine.ts` 的 `applyPlan` / `applyProjectPlan` 一次性执行全部 steps；无「执行一步 → 校验 → 再下一步」的流程，也无服务端参与的校验与回滚。

### 8.2 目标

- 可选模式：Agent 或后端「分步执行」计划，每步执行后校验（如类型、非空、表达式错误），失败则回滚该步并重试或报告。
- 或：先在服务端/沙箱做 dry-run，再把「验证过的」计划交给前端 Apply。

### 8.3 建议改动

1. **后端**
   - 在 `server` 中实现与 `engine.ts` 同构的步骤执行（或复用一份共享的「步骤语义」描述，由后端用 Python 执行）。
   - Agent 工具集中提供 `execute_step(step, table_data)`、`rollback_last_step()`，Agent 在循环中可「执行 → 观察结果 → 决定是否继续/回滚」。

2. **前端**
   - 新增「分步执行」模式：每步调用后端执行并返回该步结果与 diff，前端只做展示与「确认下一步」或「回滚」；或由后端一次性返回「已执行结果」与最终状态。

3. **优先级**：中。对复杂计划与生产环境更安全。

---

## 九、工具与能力扩展

### 9.1 现状

- 无工具；LLM 仅通过 prompt 中的 schema/sample 推断，无法主动「查某一列分布」「试跑一个表达式」。

### 9.2 目标

- 提供只读或只校验类工具，供 Agent 在生成计划前/中调用，例如：
  - `get_schema`、`get_sample_rows`、`get_column_stats`（min/max/distinct 等）；
  - `validate_expression(expr, sample_row)`、`suggest_date_format(column, sample_values)`。

### 9.3 建议改动

1. **后端**
   - 在 `server/app/services/tools.py` 中实现上述工具，并在 Agent 的 LLM 调用里以 function calling 形式暴露。
   - 工具实现可先基于请求里传来的 `schema` / `sampleRows` 或完整 table 数据，不必先接真实 DB。

2. **优先级**：中。与「执行模式」中的工具调用一起做，体验更完整。

---

## 十、计划迭代与 Refinement 循环

### 10.1 现状

- 仅当 LLM 返回非合法 JSON 时重试一次（`_parse_and_validate_plan`），无「根据执行结果或业务校验再改计划」的循环。

### 10.2 目标

- 执行完部分或全部步骤后，若校验失败（如 parse_date 格式不对、表达式报错），将错误信息反馈给 LLM，让其输出修正后的 plan（或修正后的 step），再执行。

### 10.3 建议改动

1. **后端**
   - 在分步执行或 dry-run 路径中，若某步失败，将 `(step, error_message, context)` 作为新消息交给 LLM，请求输出 `revised_step` 或 `revised_plan`，再重试。

2. **前端**
   - 若后端返回「部分执行失败 + 建议修正」，可展示 diff 与错误，并提供「使用建议修正后重试」的按钮。

3. **优先级**：低。可在分步执行与工具能力稳定后再做。

---

## 十一、实施顺序建议

1. **第零阶段（骨架）** — ✅ **已完成**
   - **AgentState**（`app/agent/state.py`）：tables、messages、applied_plans_summary、current_turn、max_turns 等；`initial_state_from_plan_request` / `from_project_request`。
   - **动作枚举**（`app/agent/actions.py`）：`call_tool` / `output_plan` / `ask_clarification` / `finish` 及 payload（CallToolPayload、Plan、ClarificationPayload、FinishPayload）。
   - **decision 函数**（`app/agent/decision.py`）：`decision(state) → (new_state, action)`，组 messages、调 LLM、解析 → OutputPlanAction 或 FinishAction。
   - **Agent 循环**（同文件）：`run_agent_loop(initial_state) → (state, action)`；call_tool 占位 stub 已预留，便于接入真实 tools。

2. **第一阶段（高优先级）** — ✅ **已完成**
   - ✅ 引入 **工具集**（get_schema、get_sample、validate_expression 等）与 **LLM 的 tool calling**（见 `app/services/tools.py`、`llm.call_llm_with_tools`）。
   - ✅ 在骨架上跑通 **Agent 循环**（多轮 LLM + 工具执行）和新路由 **`/api/agent`**（请求体同 `plan-project`，返回 plan 或 422/ clarification）。
   - 增加 **流式输出 + SSE**（与动作枚举对齐：`tool_call`、`plan_done`、`clarification` 等），前端展示推理与工具调用过程。

3. **第二阶段（中优先级）** — ✅ **已完成**
   - **多轮对话与记忆**：在 AgentState 中加入 conversation / applied_plans_summary，请求体带历史、后端拼进 messages。
   - **澄清/确认**：动作枚举已有 `ask_clarification`，前端处理并再请求。
   - **分步执行与回滚**：后端可执行步骤并校验，可选地支持回滚或 dry-run。

4. **第三阶段（按需）**
   - **计划 refinement 循环**：根据执行错误自动或半自动修正计划。
   - **项目级记忆持久化**：存 DB/文件，下次打开项目时加载。

---

## 十二、小结

| 修改点           | 主要涉及目录/文件 |
|------------------|-------------------|
| 多步推理 + 工具 | `server/app/services/llm.py`, `tools.py`(新), `api/routes/plan.py` 或 `agent.py`(新) |
| 流式与可观测   | `server/app/services/llm.py`, 新 SSE 路由, `client/src/llm.ts`, `App.tsx` |
| 对话与记忆     | `server/app/services/prompts.py`, `api/routes/plan.py`, `client/src/App.tsx`, `llm.ts` |
| 澄清与确认     | `server/app/models/plan.py`(或 agent 响应模型), 新 API 协议, `App.tsx` |
| 分步执行与回滚 | `server` 执行引擎(新), `engine.ts` 或与后端协同, `App.tsx` |
| 工具与能力     | `server/app/services/tools.py`、`/api/agent` |
| 计划迭代       | Agent 循环内 + 错误反馈协议 |
| **实现骨架**   | **已实现**：`app/agent/state.py`、`actions.py`、`decision.py`（含 `run_agent_loop`） |

按上述顺序推进：**骨架（第三节）已落地**；下一步在骨架上挂工具、流式、记忆与澄清，并可选地新增 `/api/agent` 或将现有 plan 路由切换为 `run_agent_loop`，在不破坏现有「单表/多表计划 + Diff + Apply」的前提下，逐步让系统更像一个可观测、可对话、会使用工具的 **表格编辑 Agent**。
