# Cursor for Spreadsheet — MVP

基于 **Cmd+K 式工作流** 的表格编辑 Demo：带上下文的自然语言 → LLM 生成**结构化执行计划** → **Diff 预览** → 一键 **Apply** 写回表格。

## 功能概览

### 已实现（MVP）
1. **Cmd+K「AI 编辑」弹窗**：输入自然语言，携带当前表结构 + 样本行作为上下文。
2. **单表计划**（`/api/plan`）：
   - `add_column`：新增派生列（JS 表达式，可引用 `row`）
   - `transform_column`：对已有列做清洗（trim / lower / upper / replace / parse_date）
3. **多表 / 项目计划**（`/api/plan-project`）：
   - 上述单表能力 + `join_tables`、`create_table`
4. **Diff 预览**：展示将新增/修改的列。
5. **一键 Apply**：在浏览器内执行计划并写回表格。
6. **撤销**：基于快照的「撤销上一次 Apply」。
7. **Agent 模式（实验性）**：
   - 后端提供 `/api/agent`：基于同一份多表上下文，使用多轮 LLM + 工具（schema / 样本 / 列统计 / 表达式校验等）生成计划，并在遇到歧义时返回「澄清问题」而不是直接执行。
   - 提供 `/api/agent-stream`（SSE）：以流式事件（`tool_call` / `tool_result` / `plan_done` / `finish` / `clarification`）推送 Agent 执行过程，便于前端实时展示「模型在做什么」。

### 刻意不做（当前范围）
- 协同编辑
- 完整公式引擎 / Excel 兼容
- 多表血缘图
- 外部数据源连接

---

## 环境要求

- **Python 3.10+**（后端）
- **Node.js 18+**（前端构建）

---

## 快速开始

### 1) 配置 LLM

- **云端（OpenRouter）**：在 [OpenRouter](https://openrouter.ai) 创建 API Key。
- **本地（Ollama）**：安装并启动 [Ollama](https://ollama.ai)，拉取所需模型。

### 2) 启动后端

```bash
cd server_py
cp .env.example .env
# 编辑 .env：设置 OPENROUTER_API_KEY、OLLAMA_MODEL 等
pip install -r requirements.txt
uvicorn main:app --reload --port 8787
```

后端地址：**http://localhost:8787**

> 使用本地 Ollama 时，建议关闭本机 VPN，避免请求 `localhost:11434` 被代理导致 503。

### 3) 启动前端

新开终端：

```bash
cd client
npm install
npm run dev
```

在浏览器打开 Vite 给出的地址（一般为 **http://localhost:5173**）。

---

## 示例提示词

- `Add a column total_price = price * quantity`
- `Transform column email to lowercase`
- `Trim whitespace in column name`
- `Replace "-" with "" in column phone`
- `Parse column signup_date as date`

---

## 架构简述

1. **前端**：收集当前表 schema、若干样本行、可选选区，发起计划请求。
2. **后端**：
   - 对于 `/api/plan` / `/api/plan-project`：用 LLM 直接生成**仅含 JSON** 的执行计划（`intent` + `steps[]`）。
   - 对于 `/api/agent` / `/api/agent-stream`：额外携带历史对话与已应用计划摘要，由 Agent 多轮调用 LLM 与工具，必要时先向用户发起「澄清」再给出计划。
3. **前端**：校验计划、渲染 Diff，Apply 时在浏览器内运行内置的转换引擎执行步骤（包括 Agent 产出的计划）。

### 后端结构（server_py）

- **`app/`**：FastAPI 应用
  - `api/routes/`：
    - `plan`：单表 / 多表计划（一次调用直接返回 plan）
    - `agent`：Agent 模式，同样的上下文但通过多轮 LLM + 工具生成 plan，可返回澄清问题
    - `agent-stream`：Agent 模式的 SSE 流式版本，推送 tool_call / tool_result / plan_done / finish / clarification 等事件
    - `export`、`health`、`config`
  - `services/`：
    - `prompts`：提示词与 `Message` / `build_messages`
    - `llm`：Ollama / OpenRouter 调用，包含带 tools 的调用 `call_llm_with_tools`
    - `tools`：Agent 可用工具（读 schema / 样本、列统计、表达式校验、分步执行/回滚占位等）
  - `models/`：
    - 计划与请求/响应模型（`PlanRequest` / `ProjectPlanRequest` / `PlanResponse`）
    - Agent 请求模型 `AgentProjectPlanRequest`（在多表请求基础上增加 `history` 与 `appliedPlansSummary`）
  - **`agent/`**：Agent 骨架（状态、动作、决策、循环）
    - `state.py`：`AgentState`（包含 tables、messages、applied_plans_summary、conversation 等）、`TableContext`、`initial_state_from_*`
    - `actions.py`：动作枚举（`call_tool` / `output_plan` / `ask_clarification` / `finish`）及各类 payload
    - `decision.py`：`decision(state) → (state, action)`（支持 tools 与澄清）、`run_agent_loop(initial_state) → (state, action)`
- **入口**：`uvicorn main:app`，`main.py` 挂载 `app.main.app`。

---

## 安全与正确性（Demo 说明）

- `add_column` 的表达式通过 `new Function("row", ...)` 在浏览器中执行，**不适合生产**；生产环境应使用沙箱表达式或服务端执行。
- LLM 输出需校验与清洗（当前有 JSON 提取与重试逻辑），不可直接信任。
