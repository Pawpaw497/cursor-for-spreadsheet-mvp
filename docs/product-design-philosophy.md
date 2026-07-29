# 产品设计理念（Product Design Philosophy）

> 本文是对整个仓库（`README.md`、`docs/`、`.cursor/rules`、`.cursor/plans`、`server/app/agent/**`、`client/src/**`）的一次通读式归纳：**这个项目在信什么、为什么这么切分、什么刻意不做**。
> 它不描述接口细节——契约看 [`architecture.md`](architecture.md)、[`plan-step-types-reference.md`](plan-step-types-reference.md)、[`agent-memory.md`](agent-memory.md)；本文只负责"为什么"。

---

## 0. 一句话

**把电子表格编辑变成一次可解释、可预演、可撤销的 Agent 决策**——而不是让 LLM 直接吐出一张新表。

---

## 1. 项目定位：可展示的 Agent 沙盒，不是商业产品

README § Project intent 与 `.cursor/rules/workspace-core.mdc` 反复写死同一件事：这是**一个维护者长期演进的、可被展示/fork/扩展的个人项目**，用途是把当下的 agent 技术、策略与哲学（工具循环、澄清、结构化计划、记忆）落在一个真实可用的表格 UX 上。

由此派生出三条贯穿全仓的取舍：

- **深度 > 广度**：宁可把「澄清」做成规则轨 + LLM 软扫描 + `ask_user` 三条路径并存并写清互斥时序，也不去堆功能数量。
- **可读性即交付物**：代码、`docs/`、plan 文件都按「能被别人读懂并复现推理过程」的标准写；维护者的内部演进笔记甚至把架构对比与差距分析原样留档。
- **明确的非目标**：协同编辑、完整公式引擎、多表血缘图、外部数据源连接——在 README、`architecture.md`、`features.md` 三处重复声明为 intentional non-goals，防止范围漂移。

许可证（CC BY-NC 4.0）与「local-first、非生产加固、`add_column` 走浏览器 `new Function`」的安全声明，是这个定位的诚实延伸：**不假装是产品**。

---

## 2. 核心产品命题：Structured Plan 是唯一的中间物

README 的反问句是整个产品的地基：

> **Why not output CSV directly?** — `Structured Plan + Agent clarification + interpretable Diff + undo` vs `opaque chat-to-CSV`。

LLM 的输出不是数据，而是一份 **JSON Plan（`intent` + `steps[]`）**。这一个决定同时买到了四样东西：

| 能力 | 因为 Plan 是结构化的，所以…… |
|------|------------------------------|
| **可预演** | 可以在拷贝表上 dry-run，算出 diff 再给人看 |
| **可解释** | 每一步是具名 action（`add_column`/`join_tables`/`lookup_column`…），能逐条渲染而非"AI 改了点东西" |
| **可校验** | Pydantic / Zod 双端类型校验；校验失败可回灌重试（PR #43），而不是把坏结果交给用户 |
| **可撤销** | Apply 前快照 + 工具栏撤销 |

**推论：Plan 契约是全仓最稳定的东西。** `.cursor/rules/workspace-core.mdc` 与 `CLAUDE.md` 都写明「前后端 JSON plan 契约稳定，除非明确要求否则保持 preview/apply 行为」。前端 `types.ts`/`engine.ts` 与后端 `plan.py`/`plan_executor.py` 是同一份语义的两处实现——项目自己也把这份重复列为已知风险（见 §7）。

---

## 3. 交互哲学：不确定就问，不猜

「Clarification before action」被提升到与 Plan 同级的产品原语，而不是错误处理的兜底。三条路径按**成本从低到高、时机从早到晚**排布：

| 轨道 | 时机 | 动机 |
|------|------|------|
| **LLM 软扫描**（`clarify_scan`） | Plan 生成**前** | 语义模糊（join 键不明、指代不清）时，省掉一次完整主模型 Plan 生成的成本 |
| **`ask_user` 工具** | 主 Agent 推理过程中 | 让模型自己有权喊停 |
| **规则轨**（`maybe_need_clarification`） | Plan 生成**后**的确定性 gate | 逻辑硬伤（多表写步骤缺 `table`、列引用歧义）的兜底，不依赖模型自觉 |

设计上的关键是**它们时序不同因而不互相取代**：软扫描命中就直接返回澄清、根本不进 `llm_decide`；未命中则正常出 Plan，仍可能被 post-plan 规则轨拦下。

同样重要的反向规则：**当界面已经消歧时不要多嘴**。`context.activeTable` / `focusedColumn` 存在时，规则轨会跳过澄清——澄清是为了减少错误，不是为了表演谨慎。

---

## 4. 信任模型：确定性守着 LLM，而不是反过来

全仓最一致的工程哲学是：**把 LLM 放在"提建议"的位置，把不可协商的正确性交给确定性代码。**

具体形态：

- **守护规则（guards）**：Pre-plan 语义裁剪允许 LLM 挑表，但 `activeTable` 与用户选区覆盖的表，其 schema/profile **强制全量保留**，LLM 裁剪不了。
- **Fail-open + fallback，而不是 fail-hard**：`intent_analyzer` 失败就把语义字段留空继续走；`pre_plan` 超时（0.8s）就回退全量 Context；`clarify_scan` 超时就放行。**辅助能力永远不能变成主路径的单点故障。**
- **失败要快、要有类型**：空回复 / Plan 校验失败映射为稳定的 `422` / `502`；`finish_reason='error'` 的 10 分钟挂起被专门修成 fast-fail。宁可明确报错，不要静默劣化。
- **绝不静默猜测**：eval 用例明写「即使直接出 Plan，每个 write step 也必须显式指定合法 `table`，禁止 silent 缺表」。

**"Ask before destructive"** 在 `CLAUDE.md`、`workspace-core.mdc`、agent 子代理定义里各写了一遍——对用户数据的破坏性操作，默认要么先问、要么先预演。

---

## 5. 上下文哲学：带宽是稀缺资源

维护者的内部演进笔记把这条讲得最透：非 MVP 阶段的挑战不是"能不能出 Plan"，而是**在长上下文、多表、多轮中保持确定性、经济性与自进化能力**——核心手段是**带宽管理**与**任务解耦**。

落到当前代码：

- **分层供给上下文**：`context_analyzer` 先用确定性统计算出 `DataContext`（列级统计、共享判别列）→ `intent_analyzer` 用一次批量 LLM 调用为每张表回填 `topic`/`description`/`granularity`（按表结构签名做进程内缓存）→ `pre_plan` 再做表级裁剪。**便宜的先算，贵的按需算，结果可缓存。**
- **行数据按需拉取**：`sample_rows` 概念已被移除，需要具体行走 `peek_range` 工具——**不预先塞，按需查**。
- **压缩而非截断**：长会话走 middle-out compaction（`memory_compaction.py` / `memoryCompaction.ts`）。
- **非决策任务异步化**：`preview_summary`（把 diff 翻译成人话）通过 `asyncio.create_task` 生成，首包 `summary` 恒为 `null`、随后由 `preview_summary_ready` SSE 补发。**摘要迟到可以，首包变慢不行。**

规划中的三层记忆（Index → Topic Files → Grep-only Logs）和辅模型层（`aux_llm`）是同一条思路的延长线：**主 Planner 的 context 是最贵的资源，任何能挪出去的都挪出去。**

---

## 6. 记忆哲学：三种"历史"必须分开

`agent-memory.md` 立了一条很硬的边界，值得单独拎出来，因为很多项目会把它们混成一锅：

| 系统 | 存什么 | 进 prompt 吗 |
|------|--------|--------------|
| **Memory** | 压缩后的会话/工作区状态 | **是**——产品 SSOT |
| **Audit**（SQLite `http_request_logs` / `llm_call_logs`） | 原始 HTTP/LLM 请求响应 | **否**——纯可观测性 |
| **Checkpoint**（LangGraph） | 图运行时状态 | 间接——编排基础设施 |

配套立场：**记忆是浏览器优先（browser-first）的**，`WorkspaceMemory` 存在 `localStorage`，服务端会话备份是**可选**的（`SESSION_MEMORY_DB_ENABLED=1`，默认关）。这既符合 local-first / 隐私声明，也让"后端重启不丢对话"成为可实现的产品承诺（`lastServerBootId` 变化时显示恢复横幅）。

---

## 7. 架构纪律：单一 Agent 栈，禁止平行运行时

`.cursor/rules/agent-build-practices.mdc`、`CLAUDE.md`、`.cursor/agents/spreadsheet-agent-navigator.md` 三处用几乎相同的措辞立规矩：

> 通过**扩展 tools、prompts、typed state、validation、orchestration 分支**来增加能力；**不要**引入第二个调度器、消息总线、自定义工具协议或独立 Agent 运行时。
>
> Good: 加一个 tool，结果经现有 decision/orchestration state 消费。
> Bad: 另起一套 `while True: llm -> parse`。

配套的工程纪律同样成文化：typed models / explicit state / deterministic validation 优于隐式全局与临时解析；聚焦编辑、不做顺手重构；不回滚脏工作区里用户的无关改动；新功能**测试先行**（一条 happy path + 空输入/非法数据/歧义/边界/失败路径这几类边缘用例）。

项目**自己承认**的最大架构债也在这里：预览引擎（后端 Python `plan_executor`）与执行引擎（前端 TS `engine.ts`）是两套独立实现，存在语义 drift 风险——因此 backlog 里排了 `server-authoritative-execution`（后端接管执行与撤销栈，前端退化为渲染）。**把重复实现记为债并排期，而不是假装它不存在**，本身就是这套设计理念的一部分。

---

## 8. 演进哲学：路线图分阶段，且用真实数字验收

演进被拆成四个有明确主题的阶段（`.cursor/plans/INDEX.plan.md`）：

1. **P1 v2 Core** — 能用：Pre-plan 裁剪、Preview NL 摘要、双轨澄清（已落地）
2. **P2 Infrastructure & YOLO** — 快与省：辅模型服务 + 快慢路径
3. **P3 Tiered Memory** — 长效：Index / Topic / Fact 三层记忆
4. **P4 Skills & Recipes** — 复利：把成功的多步 Plan 固化成可复用 recipe，让 Agent 越用越聪明

两条不可少的配套原则：

- **验收靠 eval，不靠口头**：每落地一项就跑 `python -m evaluation`，在 `docs/evaluation.md` 的 Baseline 表**追加一行**（通过率 + 平均耗时）。评估分四层：结构正确性 / 执行正确性（真跑一遍执行引擎）/ 行为正确性（歧义场景该澄清就得澄清）/ 可观测指标（只记录不判定）。
- **负面结论也算结论**：0.8s 超时阈值校准的结论是**不调**——因为 bench 显示三模型 9/9 的多表用例全部命中 fallback，快路径从未真正走通。这条"快路径其实没赚到"的观察被完整写进 baseline 表而不是被删掉。**留下失败实验的记录，比留下漂亮的数字更有价值。**

---

## 9. 一页速查

| 主张 | 体现 |
|------|------|
| Plan 是唯一中间物 | 结构化 JSON → dry-run → diff → apply → undo |
| 不确定就问 | 软扫描（前）+ `ask_user`（中）+ 规则 gate（后），界面已消歧则不问 |
| 确定性守着 LLM | 选中表守护、fail-open fallback、类型化快速失败、禁止 silent 猜测 |
| 带宽是稀缺资源 | 分层上下文、按需 `peek_range`、middle-out 压缩、辅助任务异步化 |
| 三种历史分开 | Memory 进 prompt / Audit 不进 / Checkpoint 是基础设施 |
| 单一 Agent 栈 | 扩展 tools 与图节点，不造平行运行时 |
| 演进要有数字 | eval 四层判定 + Baseline 追加记录 + 保留负面结论 |
| 诚实的边界 | 明确非目标、明确安全限制、明确已知架构债 |

---

## 相关文档

- [architecture.md](architecture.md) — 组件、API 面、执行路径
- [agent-memory.md](agent-memory.md) — 记忆契约（本文 §6 的来源）
- [evaluation.md](evaluation.md) — eval 套件与评估标准（本文 §8 的来源）
- [features.md](features.md) — 当前已实现能力清单
- [plan-step-types-reference.md](plan-step-types-reference.md) — Plan JSON 契约
