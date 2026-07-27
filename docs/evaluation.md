# Agent 评估体系（Eval Suite）

**代码位置**：[`server/evaluation/`](../server/evaluation/)（`cases.py` 用例定义、`runner.py` 执行器、`__main__.py` CLI）。

## 目的与定位

`test-data/test-prompts.md` 是给人工浏览、手动粘贴到 Cmd+K 里试的 prompt 目录；`server/tests/test_cloud_llm_sample_e2e.py` 只验证「Plan 能解析、`steps>=1`」。两者都不能回答「这次改动是让 Agent 变好了还是变差了」。

本套件把 `test-data/test-prompts.md` 里已验证过的场景固化成**可运行、可断言、能被后续功能开发引用**的评估用例：调用 LLM 生成 Plan → 用真实执行引擎跑一遍 → 检查产出是否符合业务预期。定位是「与项目定位相匹配」的轻量评估——不是企业级 eval 平台，不接第三方评测服务，纯 Python 标准库。

## 评估标准

分四层，前三层是每个用例的 pass/fail 判定依据，第四层只记录、不判定：

1. **结构正确性**——LLM 返回内容能被 `Plan` Pydantic 模型解析通过；用到的 `action` 集合覆盖该场景的 `required_actions`。
2. **执行正确性**——生成的 Plan 交给 `POST /api/execute-plan`（与前端 Apply 同一条执行路径）真实跑一遍，检查产出表的列、行数、排序、分类取值等是否符合业务预期，而不是只看 Plan JSON 长得像不像。
3. **行为正确性**——对故意设计的歧义场景（如多表场景下不指定目标表），Agent（`/api/agent`）应触发 `clarification` 而不是静默猜测。
4. **可观测指标（不判定通过/失败）**——每个用例记录耗时（`elapsed_ms`）与 Plan step 数（`step_count`），为后续性能类优化提供前后对比基线。

## 运行方式

默认走本地 Ollama，与 README 推荐的 Quick start 路径一致（需要 `ollama serve` 且已 `ollama pull qwen2.5:7b`）：

```bash
cd server
uv run python -m evaluation
```

云端示例：

```bash
uv run python -m evaluation --model-source cloud --cloud-model-id <openrouter-model-id>
```

其他选项：

```bash
uv run python -m evaluation --case sales_amount_filter_sort   # 只跑指定用例，可重复传
uv run python -m evaluation --json-out /tmp/eval-report.json  # 落一份机器可读结果，方便手工前后对比
```

> 与 `RUN_CLOUD_LLM_E2E` 的口径一致：本套件会真实调用 LLM，**不进 `make test` / CI 默认路径**，需要本地 Ollama 运行中或配置 `OPENROUTER_API_KEY`。个别用例可能因 LLM 输出的随机性偶发失败，属预期内噪声；连续失败才需要关注。

## 用例格式与新增指南

用例是 `server/evaluation/cases.py` 里 `CASES: list[EvalCase]` 的一项：

| 字段 | 含义 |
|------|------|
| `prompt` | 发给 Agent 的自然语言指令 |
| `target_tables` | 从 `sample.xlsx` 里选哪些表喂给这次请求 |
| `required_actions` | Plan 至少要用到的 Plan step `action` 集合 |
| `min_steps` | Plan 步骤数下限 |
| `ambiguous_target` | 模糊请求：runner 统一走 `/api/agent`（无独立 `endpoint` 字段），既接受 Agent 主动触发澄清（`kind="clarification"` 且带 `options`），也接受直接出 Plan——但此时每个 write step 必须显式指定合法 `table`（禁止 silent 缺表/指向不存在的表） |
| `check` | `(EvalRunContext) -> list[str]`，对执行后的表做业务断言，返回失败原因列表（空列表=通过） |

**新增 Plan step 类型或 Agent 能力时，应在 `CASES` 中至少补一条用例**，并在其 `check` 里断言新能力的产出符合预期。

## 与后续功能提升的衔接

这套 eval 是 `docs/agent-improvements.md`（路线图）与 `docs/agent-design-evolution.md`（架构演进方案）落地时的验收/回归工具，而不是一次性摆设：

- **路线图每落地一项**（`agent-improvements.md` 十一节「实施顺序建议」、`agent-design-evolution.md` 各 Phase），落地者应跑一次 `python -m evaluation`，并在下方「Baseline 记录」追加一行——用真实通过率而不是口头描述来证明「变好了/没退步」。
- **澄清场景扩展**（`agent-improvements.md` 第七节，例如多列同名、复杂 join 条件不明确等）落地时，应在 `cases.py` 追加同类 `ambiguous_target=True` 用例。
- **分步执行/回滚、工具集扩展、YOLO 快慢路径**（`agent-design-evolution.md` §4.2）等涉及耗时/轮次的改动，可以扩展 `EvalCaseResult`（目前已有 `elapsed_ms`、`step_count`）记录工具调用数/轮次，用于前后对比而非只看是否通过。

### Baseline 记录

| 日期 | 路线图项 | 通过率 | 平均耗时 | 备注 |
|------|----------|--------|----------|------|
| 2026-07-08 | 首次建设本评估套件（`--model-source cloud --cloud-model-id google/gemini-2.5-flash-lite`） | 1/5 (20%) | 4 个 plan_project 用例约 6s/case；`ambiguous_add_column_needs_clarification` 因下条 bug 卡 601s | `sales_amount_filter_sort`/`dept_budget_usage_rate`/`dept_risk_flag_create_table` FAIL（业务断言不通过，反映 gemini-2.5-flash-lite 在本项目 prompt 下的真实基线质量）；`ambiguous_add_column_needs_clarification` ERROR——发现真实 bug：OpenRouter 偶发返回 `finish_reason='error'`，`/api/agent` 未 fast-fail，重试到 `max_turns` 才 422，耗时 10 分钟。该 bug 待建路线图项修复后重新建立 baseline |
| 2026-07-25 | Agent Evolution Phase 1（pre_plan 裁剪 + preview_summary 异步 + 澄清双轨，`agent-evolution-p1-v2-core.plan.md`；`--model-source cloud --cloud-model-id google/gemini-2.5-flash-lite`） | 1/6 (17%) | 平均 198s/case（含 1 个 649s 异常值）；`ambiguous_add_column_no_silent_target` 唯一通过用例仅 4.4s | 新增确定性防线按设计工作：`clarify_scan`/`pre_plan` 在本次真实压测下均按 800ms 预算超时并 fail-open/fallback 全量表，未拖垮或误判下游流程（日志可见 `clarify_scan: timed out ... fail-open` → `pre_plan: selection timed out ... fallback to all tables` 依次触发，符合设计）。`sales_amount_filter_sort`/`category_summary_multitable`/`ambiguous_join_key_semantic_clarify`（新用例）ERROR、`dept_budget_usage_rate` 649s ERROR——均为下游 `pa_decision_step` 的 `llm_error: `（空 reason，`asyncio.timeout` 触发的 `TimeoutError` str 为空）；其中一次异常栈追出根因是 `intent_analyzer` 内 `json.decoder.JSONDecodeError: Expecting value`，即 OpenRouter 返回了非完整 JSON 响应体——触发条件与 2026-07-08 那条 `finish_reason='error'` bug 不同（响应体本身不是合法 JSON，而非模型显式返回 error）。**2026-07-26 补充观测**：同一套件同模型下 `finish_reason='error'` 复现了一次，但 6.2s 就 fast-fail 返回 422（`current_turn=0`，未重试到 `max_turns`），对比 2026-07-08 的 601s——即两者现在**行为不同而非类似**，`finish_reason` 那条已被覆盖，本条 JSON 解析失败仍会拖到超时，说明现有 fast-fail 覆盖面不足。附带发现：该次 fast-fail 的实际报错是 OpenAI SDK 反序列化 `ChatCompletion` 时的 pydantic `literal_error`（`choices.0.finish_reason` 非法字面量），发生在 pydantic-ai 调用 `_SafeOpenAIChatModel._map_finish_reason` 之前，故 PR #30 那个 override 在此路径上可能并未被走到（其单测 `test_safe_model_raises_on_error_finish_reason` 为直接调用该方法的隔离测试，未覆盖真实响应路径）；用户可见行为正确，但若后续 SDK 放宽该校验，兜底是否仍在需另行验证。**上述 JSON 解析失败（非 `finish_reason` 那条）是新发现的独立 bug，尚无对应任务，需要单独排查/建路线图项，不在本次 P1 范围内修复**。`dept_risk_flag_create_table` FAIL（业务断言不通过，同 2026-07-08 已知基线质量问题）。样本量小（6 用例，1 次运行），噪声不能排除，但至少证明 P1 新增的两处超时防线在真实延迟下没有引入新的可观测故障 |
| 2026-07-27（pre-tuning，google/gemini-2.5-flash-lite） | pre_plan/clarify_scan 0.8s 超时阈值校准前观测（`pre-plan-clarify-scan-timeout-tuning.plan.md`；限于成本，只重跑 3 个多表 case：`category_summary_multitable`/`ambiguous_add_column_no_silent_target`/`ambiguous_join_key_semantic_clarify`，非全部 6 个） | 2/3 (67%) | 平均 8.4s/case（10.8s / 6.1s / 8.3s） | **fallback 命中率 3/3 (100%)**——本模型 3 个用例全部先后触发 `clarify_scan: timed out after 0.80s, fail-open` 与 `pre_plan: selection timed out after 0.80s, fallback to all tables`，无一次真实跑完（未超时）的裸调用记录。`ambiguous_join_key_semantic_clarify` ERROR：`llm_error: ... choices.0.finish_reason` literal_error（OpenRouter 返回非法 `finish_reason='error'`），与 2026-07-25 行记录的已知 bug 同类，非本次超时阈值改动引入 |
| 2026-07-27（pre-tuning，openai/gpt-4o-mini） | 同上 | 2/3 (67%) | 平均 10.9s/case（12.5s / 8.8s / 11.4s） | **fallback 命中率 3/3 (100%)**——同上，3 个用例全部命中 `clarify_scan`+`pre_plan` 双重 0.8s 超时。`category_summary_multitable` ERROR：`max_turns`（用满 10 轮工具调用未收敛出 Plan，与超时阈值无直接关联，需要另行归因） |
| 2026-07-27（pre-tuning，deepseek/deepseek-v4-pro） | 同上 | 2/3 (67%) | 平均 24.9s/case（51.6s / 6.6s / 16.5s，51.6s 为异常值） | **fallback 命中率 3/3 (100%)**——同上，3 个用例全部命中 `clarify_scan`+`pre_plan` 双重 0.8s 超时。`category_summary_multitable` ERROR：模型对本应直接出 Plan 的用例主动返回 `clarification`（业务判断差异，非结构性故障，eval 断言未预期此分支）。**三模型合计 9/9 (100%) 多表 case 命中 fallback**——印证背景里 07-27 观察：`clarify_scan`/`pre_plan` 的"先试快速裁剪、超时再退全量"快路径在本轮多表场景下从未真正走通，只是白白多付一次注定超时的往返延迟；本行是后续 `measure-real-latency-distribution`/`decide-new-thresholds` 的前测对照基准 |
| 2026-07-27（阈值校准结论，`pre-plan-clarify-scan-timeout-tuning.plan.md` 收尾） | 决定不调阈值——见 `measure-real-latency-distribution` 隔离 bench（`server/evaluation/bench_pre_plan_clarify.py`，3 模型×3 case，n=6/组合，不含 wait_for 封顶）：`pre_plan` p90 落在 1.75s-9.0s，`clarify_scan` p90 落在 1.8s-66s，普遍远超「3-5s 需升级汇报」门槛，且两模块同请求内串行叠加。已排除代理/VPN（curl 直连与走代理到 openrouter.ai 的 TTFB 均 ~450ms）与 prompt 过大/context 未压缩（实测 prompt 仅几百字符，无历史/工具/列级 profile）两个外部归因；延迟是 OpenRouter 路由到底层 provider 后的真实推理耗时。**用户拍板：`PRE_PLAN_TIMEOUT_S`/`CLARIFY_SCAN_TIMEOUT_S` 保持 0.8s 不变**，未满足「快路径收益 > 额外等待成本」 | N/A（未变更常量，未重跑全量 eval——见备注） | N/A | **本行不是"调优后"对照基线，而是本计划的收尾记录**：①`clarify_scan` 补齐了成功路径遥测（`clarify_scan_result`，字段对齐 `pre_plan_selection` 的 elapsed_ms/outcome/needs_clarification/table_count），并用一次真实（非 mock）`python -m evaluation` 单 case 调用验证两条日志能在生产路径正确触发；②是否将 pre_plan/clarify_scan 转向异步化（P2）作为**独立开放问题**记录，不在本计划处理范围内，跟踪见计划文件「开放问题」章节；③常量未变，fallback 命中率预期仍与 2026-07-27（pre-tuning）三行一致（~100%），故未再付费重跑三模型全量 eval 验证一个已知不会变化的数字 |
