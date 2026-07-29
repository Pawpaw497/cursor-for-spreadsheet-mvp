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

默认走云端 OpenRouter，默认模型 `deepseek/deepseek-v4-flash`（需要配置 `OPENROUTER_API_KEY`）：

```bash
cd server
uv run python -m evaluation
```

换云端模型 / 改走本地 Ollama（需要 `ollama serve` 且已 `ollama pull qwen2.5:7b`）：

```bash
uv run python -m evaluation --cloud-model-id <openrouter-model-id>
uv run python -m evaluation --model-source local
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

这套 eval 是内部路线图（维护者本地规划，见 `docs/local/`）落地时的验收/回归工具，而不是一次性摆设：

- **路线图每落地一项**，落地者应跑一次 `python -m evaluation`，并在维护者本地的 Baseline 记录里追加一行——用真实通过率而不是口头描述来证明「变好了/没退步」。
- **澄清场景扩展**（如多列同名、复杂 join 条件不明确等）落地时，应在 `cases.py` 追加同类 `ambiguous_target=True` 用例。
- **分步执行/回滚、工具集扩展、快慢路径**等涉及耗时/轮次的改动，可以扩展 `EvalCaseResult`（目前已有 `elapsed_ms`、`step_count`）记录工具调用数/轮次，用于前后对比而非只看是否通过。
