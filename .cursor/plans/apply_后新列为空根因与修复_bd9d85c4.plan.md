---
name: Apply 后新列为空根因与修复
overview: 根因是：LLM 返回的 add_column.expression 为完整 JavaScript 箭头函数（如 `row => row.订单号.split('-')[0]`），前端与服务端按“仅表达式体”的方式执行，导致前端预览返回函数对象、服务端 eval 报语法错误并返回 None，新列因此为空。修复需统一规范化 expression（strip 箭头取 body），并在服务端用可属性访问的 row 对象执行 Python 表达式。
todos: []
isProject: false
---

# Apply 后新列为空 — 根因分析与修复计划

## 现象

- 用户提示：「把订单号拆分成两列」
- Plan 正常：两个 `add_column`，expression 分别为 `row => row.订单号.split('-')[0]` 与 `row => row.订单号.split('-')[1]`
- Diff 显示新增列「订单前缀」「订单后缀」
- 点击 Apply 后，这两列**全部为空**

## 根因分析

### 1. 约定与实现不一致

- 提示词约定（[server/app/services/prompt_content.py](server/app/services/prompt_content.py)）：`add_column.expression` 为 **JavaScript `(row) => expression`** 形式。
- LLM 实际返回：完整箭头字符串，例如 `row => row.订单号.split('-')[0]`。
- 前端与后端实现都按「表达式体」来用（即期望只有 `row.订单号.split('-')[0]`），没有处理「完整箭头」这一层。

### 2. 服务端（Apply 列为空的直接原因）

执行在 [server/app/services/plan_executor.py](server/app/services/plan_executor.py) 的 `_eval_row_expression`：

```96:102:server/app/services/plan_executor.py
def _eval_row_expression(expression: str, row: Mapping[str, Any]) -> Any:
    """在受限环境下对单行执行表达式，失败时返回 None（与前端 safeEval 类似）。"""
    try:
        fn = eval(f"lambda row: ({expression})", _safe_globals(), {})
        return fn(row)
    except Exception:
        return None
```

- 传入的 `expression` 为完整字符串：`row => row.订单号.split('-')[0]`。
- 实际执行的是：`eval("lambda row: (row => row.订单号.split('-')[0])", ...)`。
- `**=>` 在 Python 中非法**，产生 `SyntaxError`，被 `except` 捕获后统一返回 `None`，所以新列全部为空。

### 3. 若只去掉箭头：还有语言/结构差异

即使先去掉 `row =>`，只保留 body `row.订单号.split('-')[0]`：

- 当前传入的 `row` 是 **dict**；在 Python 里 `row.订单号` 是**属性访问**，dict 没有该属性 → `AttributeError`，仍会返回 `None`。
- 因此服务端还需要：要么让 `row` 支持「列名当属性」访问（例如用 `types.SimpleNamespace` 包装 dict），要么约定 expression 为 Python 语法并使用 `row["列名"]`。

### 4. 前端 Preview 的同类问题

[client/src/engine.ts](client/src/engine.ts) 中：

```26:31:client/src/engine.ts
      // eslint-disable-next-line no-new-func
      const fn = new Function("row", `return (${expr});`) as (
        row: Record<string, any>
      ) => any;

      nextRows = nextRows.map((r) => ({ ...r, [name]: safeEval(fn, r) }));
```

- 当 `expr` 为完整箭头 `row => row.订单号.split('-')[0]` 时，`return (row => ...)` 返回的是**箭头函数本身**，不是其执行结果。
- 因此预览时新列得到的是 function 对象，界面可能显示为空或异常。

---

## 修复方案

### 方案概要

- **统一规范化 expression**：在执行前若发现是「完整箭头」形式，则 strip 掉 `row =>` / `(row) =>`，只保留**右侧表达式体**，再交给现有执行逻辑。
- **前端**：用 strip 后的 body 构造 `return (body);`，使 `fn(row)` 得到标量值。
- **服务端**：用 strip 后的 body 做 `lambda row: (body)`，且将每行 dict 转为**支持属性访问**的 row（如 `SimpleNamespace(**row)`），再传入 lambda，这样现有 LLM 常出的 `row.订单号` 等写法在 Python 中也可用；执行异常时仍返回 `None`。

### 具体改动


| 位置                                                                                   | 改动                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **前端** [client/src/engine.ts](client/src/engine.ts)                                  | 新增 `normalizeRowExpression(expr: string): string`：若 `expr` 匹配 `^\s*(?:row                                                                                                                                                                                                                                                                                          |
| **服务端** [server/app/services/plan_executor.py](server/app/services/plan_executor.py) | 新增 `_normalize_row_expression(expression: str) -> str`：用正则去掉 `(row)=>` / `row=>`，保留右侧 body。在 `_eval_row_expression` 内：先 `body = _normalize_row_expression(expression)`，再 `eval("lambda row: (" + body + ")")`；调用 `fn(row)` 时传入 `row = SimpleNamespace(**dict(row))`（对非 dict 的 Mapping 先转成 dict），这样 `row.订单号` 等可访问。若某行构造 SimpleNamespace 或执行表达式失败，则保持当前行为返回 `None`。 |


### 边界与兼容

- 若 LLM 以后只返回 body（无箭头），当前正则不匹配则原样使用，行为与现在一致。
- 中文列名在 Python 中为合法标识符，`SimpleNamespace(**row)` 可正常使用。
- 不在本方案中改提示词或 LLM 输出格式，仅在执行侧兼容「完整箭头」与「仅 body」两种形式。

### 验证建议

- 单表：Plan 中 `add_column` 的 expression 为 `row => row.订单号.split('-')[0]`，Apply 后新列应有正确拆分值。
- 多表 / 项目：同上，且带 `table: "销售订单"` 的 step 应对正确表生效且新列非空。
- 前端：同一 Plan 下 Diff 预览中两列应显示拆分后的字符串，而不是空或 `[object Function]`。

