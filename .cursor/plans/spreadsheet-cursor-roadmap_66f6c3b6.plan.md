---
name: spreadsheet-cursor-roadmap
overview: 为 Cursor for Spreadsheet 制定中短期功能提升路线，围绕 AI 表格编辑体验、Agent 能力以及工程化与可靠性三个大方向分阶段迭代。
todos:
  - id: enhance-ux
    content: 实现多步 Plan 可视化与 Step 级控制，并优化 Diff/撤销体验
    status: pending
  - id: agent-stream-ui
    content: 前端接入 /api/agent-stream，展示流式 Agent 推理过程并支持澄清对话
    status: pending
  - id: agent-tools-and-refinement
    content: 完善 Agent tools（execute_step/rollback/get_column_stats）并支持 Plan refinement
    status: pending
  - id: testing-and-observability
    content: 增强前后端测试覆盖率和结构化日志/可观测性
    status: pending
  - id: config-and-docs
    content: 统一配置管理、完善架构与使用文档、规范协作流程
    status: pending
isProject: false
---

## 执行清单（Todos）

与上方 YAML `todos` 同步；**本文件为路线图合集**：推进时请拆成独立小 plan/issue 再勾选；完成后请同时更新 YAML `status`。

- [ ] 多步 Plan 可视化与 Step 级控制；优化 Diff/撤销 (`enhance-ux`)
- [ ] 前端接入 `/api/agent-stream`，流式展示推理与澄清 (`agent-stream-ui`)
- [ ] 完善 Agent tools 并支持 Plan refinement (`agent-tools-and-refinement`)
- [ ] 增强测试覆盖与结构化日志/可观测性 (`testing-and-observability`)
- [ ] 统一配置、架构/使用文档、协作流程 (`config-and-docs`)

### Cursor for Spreadsheet 功能提升总体规划

结合当前代码与文档，项目已经具备：多表 Cmd+K AI 编辑、前后端同构 Plan 执行引擎、基本 Agent 骨架与工具、Chat/History 视图、本地/云端模型切换等能力。接下来建议围绕以下三个大方向演进：

- **大方向 A：AI 交互与表格编辑体验增强（面向最终用户）**
- **大方向 B：Agent 能力完善与多轮智能编排（面向复杂任务）**
- **大方向 C：工程化、可靠性与可观测性提升（面向生产级 Demo）**

下面每个方向给出 4–5 条可落地的实施步骤，便于按阶段拆分到 issue / 迭代中。

### 大方向 A：AI 交互与表格编辑体验增强

**目标**：让「自然语言 → 结构化 Plan → 可视化 Diff → 一键 Apply」这一闭环更流畅、更安全，并降低错误 Plan 对用户的影响。

- **A1：多步 Plan 可视化与 Step 级控制**  
  - 在现有 Plan Diff 区域基础上，引入 Step 列表视图（按 `PlanStep.kind` 分组展示），支持勾选/禁用某些 Step 再预览 Diff。  
  - 在前端 `engine.ts` 增加按 Step 过滤执行的能力，并在 UI 中支持「只应用选中的 Step」。  
  - 在 `App.tsx` 中为 Step 提供自然语言摘要（利用后端 `/api/plan/summary` 或本地模板），让非技术用户也能理解。  
  - 在 History 视图中记录每次实际被应用的 Step 集合，方便回溯。
- **A2：更强的表达式与错误反馈体验**  
  - 基于现有 `validate_expression` 工具和前端表达式执行环境，在用户提交自然语言需求后，自动对关键表达式进行预执行校验，若失败则在右侧面板以红色提示并给出建议修正。  
  - 在 `engine.ts` 中为表达式执行增加结构化错误对象（行号、列名、错误原因），并在 AG Grid 中通过 cell tooltip 或 error badge 呈现。  
  - 在 `/api/plan` 返回中扩展一个可选字段 `warnings`，承载 LLM 对潜在风险操作（大规模删除、类型强制转换等）的说明，并在 UI 中以 Warning 区块展示。  
  - 在 README/用户文档中添加「常见表达式写法与注意事项」章节，降低入门门槛。
- **A3：多表视图和上下文导航优化**  
  - 在 `App.tsx` 中为多表增加「血缘/依赖」视图：基于当前项目中已有 `join_tables` / `lookup_column` / `create_table` 等 Plan 历史，构建一张简单的表间依赖图（可用 mermaid 或前端简易图组件）。  
  - 支持点击某张表时在右侧面板显示「该表最近被哪些 Plan 修改过」、「它作为输入/维表参与了哪些操作」。  
  - 在 Cmd+K 面板中展示当前上下文摘要（当前表 schema、关联表数量、最近三条 Plan 摘要），帮助用户理解当前状态。  
  - 为大表增加分页/虚拟滚动性能优化和「仅展示被修改列」的过滤视图。
- **A4：更友好的新手引导与示例场景**  
  - 在前端增加引导模式（onboarding）：首次进入时用一个 overlay 引导用户完成「导入示例 → 发起第一次自然语言请求 → 预览 Diff → Apply → 撤销」的全流程。  
  - 提供多套预置 Prompt 模板（如「清洗人名列」「统一日期格式」「从订单明细聚合按用户统计」），在 Cmd+K 面板中可一键插入。  
  - 在 `test-data/` 中再补充 1–2 组真实感更强的多表样例（如订单/用户/商品维表），并在 README 中用动图/步骤解释典型使用路径。  
  - 在 `FEATURES.md` 中同步更新 UI 交互与典型场景说明。
- **A5：操作可撤销与版本快照管理优化**  
  - 梳理当前前端快照逻辑，在 `App.tsx` 中收敛为统一的 `HistoryStack` 抽象（记录项目 ID、表集合、Plan 元数据）。  
  - 增加「查看历史快照」面板，允许用户以时间线形式浏览过去的 Apply 操作，并对任意节点进行 Diff 对比当前状态。  
  - 引入轻量级的本地持久化（localStorage/IndexedDB），在用户刷新页面后可恢复最近一次项目快照和 Plan 历史（需与现有 `/api/chat-history` 协调）。  
  - 在 README 与 `AGENT_IMPROVEMENTS.md` 中说明当前版本回溯的能力边界和未来可接入远程存储的方向。

### 大方向 B：Agent 能力完善与多轮智能编排

**目标**：把当前已经搭好的 Agent 骨架（决策循环 + tools + SSE）用起来，让 Agent 能在多轮对话中自主澄清意图、分步执行和修正计划。

- **B1：前端接入 `/api/agent-stream`，展示流式 Agent 过程**  
  - 在前端新增 Agent 模式入口（如 Chat 面板中的「切换到 Agent 模式」开关），选择后改用 `/api/agent-stream` 代替 `/api/plan-project`。  
  - 实现 SSE 客户端：监听 `tool_call`、`tool_result`、`plan_done`、`clarification`、`finish` 等事件，将其以「推理轨迹」形式在 History 标签中可视化（例如一步步展示 Agent 调用了哪些工具、看了哪些列统计）。  
  - 对 `clarification` 事件，在 Chat 视图中转成 Agent 问用户的自然语言问题，并允许用户回答后重新进入 Agent 循环。  
  - 在 `AGENT_IMPROVEMENTS.md` 中补充 Agent UI 的交互流程与状态机说明。
- **B2：完善 Agent tools 与分步执行闭环**  
  - 将 `execute_step` / `rollback_last_step` 从 stub 实现为真实逻辑：复用 `plan_executor` 的单步执行能力，维护一个后端 ProjectState 的 step-level 日志。  
  - 扩充 `get_column_stats` 的能力，如支持分组统计（group by 某列 + count/distinct），便于 Agent 发现异常值或分布。  
  - 在 Agent decision 中引入「先 validate_expression，再尝试 execute_step」的策略，把语法/运行错误收敛到工具结果中，让 LLM 有机会自我修正。  
  - 为 tools 增加 rate limit/安全白名单（列出 Agent 能够读取/修改的表和列），防止错误工具调用影响非预期数据。
- **B3：引入 Plan refinement 与对话式修改**  
  - 扩展 Agent 输出结构，使其可以在已有 Plan 基础上产生「修订 Plan」（diff 形式而非全量重写）。  
  - 在前端为 Plan 区域增加「让 Agent 优化这份 Plan」按钮，点击后把当前 Plan 与用户的补充说明一起发送给 `/api/agent`，期望 Agent 仅生成修改部分。  
  - 在 `engine.ts` 中实现对 Plan diff 的合并逻辑，并在 UI 中突出显示新增/移除/修改的 Step，让用户更容易理解二次编辑的结果。  
  - 将 Plan refinement 的前后版本记录到 History 中，以便追踪 Agent 优化的轨迹。
- **B4：对话/记忆驱动的项目级 Agent**  
  - 利用当前 `/api/projects/{id}/plan` 与 ProjectState，把 Agent 的视野从单次请求扩展到「整个项目历史」：让 Agent 能够根据之前应用过的 Plan 自动避免重复、冲突操作。  
  - 在 AgentState 中增加持久化字段（如 `project_notes`、`known_issues`），并通过 `/api/chat-history` 与前端同步，用于跨会话的项目记忆。  
  - 提供一个「项目诊断」入口：用户可让 Agent 对当前项目进行巡检（如检测重复列、命名不一致、潜在 join 错误等），输出一份只读报告 Plan。  
  - 在 `FEATURES.md` 与 `AGENT_IMPROVEMENTS.md` 中增加项目级 Agent 的使用范式与边界说明。
- **B5：模型与提示词调优工具化**  
  - 将当前 prompts 拆分为更细颗粒度的模块（如「列清洗」「聚合」「多表建模」子 prompt），并在 `prompts` 模块中增加版本号与实验标签。  
  - 为内部开发者提供一个「Prompt/Model 实验模式」：在前端增加隐藏开关，允许选择不同 prompt 版本或模型组合（如 `gpt-4.1-mini` vs 本地 `qwen`），并自动记录效果到 History。  
  - 在后端新增一个简单的 `experiments` 日志（可先写到文件），归档每次实验的 prompt、模型、结果摘要，支持后续离线分析。  
  - 在 `AGENT_IMPROVEMENTS.md` 中记录每次关键 prompt 变更与模型切换的效果评估。

### 大方向 C：工程化、可靠性与可观测性提升

**目标**：把当前 Demo 打磨成更接近「可在小团队内部真实试用」的工程基线，突出稳定性、可定位问题能力与协作开发体验。

- **C1：测试覆盖与回归保护**  
  - 为前端 `engine.ts` 和后端 `plan_executor.py` 增加系统性单元测试，重点覆盖各种 PlanStep 组合（包括边界情况，如空表、缺失列、复杂表达式）。  
  - 为 Agent decision 逻辑（`decision` / `run_agent_loop`）增加若干「脚本化」测试 case，通过 mock LLM 响应来验证状态机和 tools 调用路径。  
  - 引入简单的端到端测试（可用 Playwright / Cypress），覆盖「加载示例 → 发起请求 → 预览 Diff → Apply → 撤销」的主路径，减少 UI 回归。  
  - 在 CI 中集成测试运行（例如 GitHub Actions），并在 README 中说明如何本地运行测试。
- **C2：日志、追踪与可观测性**  
  - 在后端统一引入结构化日志（如 `loguru` 或标准库 logging + JSON formatter），为每次 `/api/plan` / `/api/agent` 调用打上 requestId、projectId、model、耗时等关键字段。  
  - 在 `AGENT_IMPROVEMENTS.md` 中定义最小可用的 observability 事件模型（如 Plan 生成成功/失败、Agent 工具调用异常、模型超时等）并在代码中接入。  
  - 为前端增加一个「开发者模式」开关，可以在右下角浮层中实时显示最近的 LLM 调用、耗时、错误等（使用现有 History 数据即可）。  
  - 针对常见错误场景（网络超时、鉴权错误、JSON 解析失败）统一错误码与用户提示，避免出现难以理解的报错。
- **C3：配置管理与环境隔离**  
  - 整理 `.env` / `example.env` 与 `server/.env.example`，统一配置项命名（如 `OPENROUTER_API_KEY`、`OLLAMA_BASE_URL`、`DEFAULT_MODEL` 等），并在 README 中补充多环境配置示例（dev/staging）。  
  - 引入更清晰的 Settings 层（如 `settings.py` 中对所有环境变量做集中校验与默认值管理），避免在路由或 service 中直接访问 `os.environ`。  
  - 提供一键本地启动脚本（如 `scripts/dev.sh`），涵盖：安装依赖、启动 server + client、可选拉起 Ollama，降低新同学上手成本。  
  - 在 `FEATURES.md` 中补充「环境配置」小节，说明本地/云端模型准备方式与约束。
- **C4：性能优化与大表支持**  
  - 基于当前 AG Grid 实现，审视数据加载与状态更新路径，减少不必要的全表重渲染（例如使用更细粒度的 `rowData` 更新与 memo 化）。  
  - 针对大表场景，引入「采样执行」策略：Plan 先在样本子集上执行并展示效果，用户确认后再对全表执行，避免长时间卡顿。  
  - 为后端 `plan_executor` 中可能的重计算逻辑（如大规模 join/aggregate）添加简要性能监控（简单计时 + 日志），帮助定位瓶颈。  
  - 在文档中标注当前 Demo 针对数据规模的建议上限与性能注意事项。
- **C5：文档、规范与协作流程**  
  - 继续维护并扩充 `FEATURES.md` 与 `AGENT_IMPROVEMENTS.md`：每次新增功能或 Agent 变更都同步整理要点与已知限制。  
  - 在 `docs/` 目录下新增一篇「架构总览」文档，配合一张 mermaid 架构图，说明前后端、Agent、LLM、工具之间的数据流与依赖关系。  
  - 引入基础的代码风格与 linter 配置（前端 ESLint+Prettier，后端 Ruff/Black），在 CI 中强制执行，以减少风格差异。  
  - 在 README 顶部增加「快速上手」与「贡献指南（Contribution Guide）」链接，帮助新成员理解如何提 PR、如何跑测试、如何做回归验证。

以上规划可以按优先级拆成若干里程碑：

- 第一阶段：优先 A1/A2 + B1，提升核心工作流体验并让 Agent 流程跑通、可观测。
- 第二阶段：推进 B2/B3/B4，强化 Agent 智能与项目级能力；并行做 C1/C2 的工程化打底。
- 第三阶段：补齐 C3/C4/C5 与 A3/A4/A5，将 Demo 打磨成可在团队内部真实试用、可长期演进的基础版本。

