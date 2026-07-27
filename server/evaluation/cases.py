"""评估用例定义：Plan 结构正确性 + 执行正确性 + 行为正确性。

用例文案对应 `test-data/test-prompts.md` 中已人工验证过的场景（注释标出对应编号），
但断言（required_actions / check）是本文件独有的，两者手工保持同步，服务不同读者：
`test-data/test-prompts.md` 给人工浏览，本文件给自动化评估。

新增 Plan step 类型或 Agent 能力时，应在 `CASES` 中至少补一条用例（见 docs/evaluation.md）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from app.models.plan import Plan


@dataclass(frozen=True)
class EvalRunContext:
    """一次用例执行后，交给 `check` 函数做业务断言的上下文。"""

    plan: Optional[Plan]
    result_tables: dict[str, dict[str, Any]]  # /api/execute-plan 返回的 tables
    new_tables: list[str]


CheckFn = Callable[[EvalRunContext], list[str]]


@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    prompt: str
    target_tables: tuple[str, ...] = ()
    required_actions: frozenset[str] = field(default_factory=frozenset)
    min_steps: int = 1
    # 模糊请求：允许澄清，或直接出 plan 但所有 write step 必须显式指定合法 table
    # （禁止的是 silent 缺表 / 指向不存在的表；模型带 Data profile 自选表是合法行为）。
    ambiguous_target: bool = False
    check: Optional[CheckFn] = None


def _schema_keys(table: dict[str, Any]) -> set[str]:
    return {str(col.get("key", "")) for col in table.get("schema", [])}


def _sorted_descending(values: list[Any]) -> bool:
    try:
        nums = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    return nums == sorted(nums, reverse=True)


def _check_sales_amount_filter_sort(ctx: EvalRunContext) -> list[str]:
    failures: list[str] = []
    table = ctx.result_tables.get("销售订单")
    if table is None:
        return ["result missing 销售订单 table"]

    cols = _schema_keys(table)
    if "金额" not in cols:
        failures.append("missing 金额 column in 销售订单")

    rows = table.get("rows", [])
    if not rows:
        failures.append("销售订单 has no rows after filter (expected some 2024 orders)")
        return failures

    if "金额" in cols:
        amounts = [r.get("金额") for r in rows]
        if any(a is None for a in amounts):
            failures.append("some rows have null 金额")
        elif not _sorted_descending(amounts):
            failures.append("rows not sorted descending by 金额")

    bad_dates = [r for r in rows if "2024" not in str(r.get("订单日期", ""))]
    if bad_dates:
        failures.append(f"{len(bad_dates)} row(s) have 订单日期 not in 2024")

    return failures


def _check_dept_budget_usage_rate(ctx: EvalRunContext) -> list[str]:
    failures: list[str] = []
    table = ctx.result_tables.get("部门预算")
    if table is None:
        return ["result missing 部门预算 table"]

    cols = _schema_keys(table)
    missing = {"使用率", "预算状态"} - cols
    if missing:
        failures.append(f"missing columns: {sorted(missing)}")

    rows = table.get("rows", [])
    if "预算状态" in cols:
        allowed = {"紧张", "正常", "宽松"}
        bad = [r for r in rows if r.get("预算状态") not in allowed]
        if bad:
            failures.append(f"{len(bad)} row(s) have 预算状态 outside {sorted(allowed)}")

    if "使用率" in cols and rows:
        if not _sorted_descending([r.get("使用率") for r in rows]):
            failures.append("rows not sorted descending by 使用率")

    return failures


def _check_category_summary_multitable(ctx: EvalRunContext) -> list[str]:
    candidates = [
        name
        for name in (*ctx.new_tables, *ctx.result_tables.keys())
        if ("类别" in name or "汇总" in name) and name not in ("销售订单", "产品信息")
    ]
    if not candidates:
        return ["no new aggregated table found (expected a name containing 类别/汇总)"]

    name = candidates[0]
    table = ctx.result_tables.get(name)
    if table is None:
        return [f"table '{name}' referenced but missing from result_tables"]

    failures: list[str] = []
    rows = table.get("rows", [])
    if not rows:
        failures.append(f"aggregated table '{name}' is empty")
        return failures

    profit_cols = [k for k in _schema_keys(table) if "毛利" in k]
    if not profit_cols:
        failures.append(f"aggregated table '{name}' has no 毛利-like column")
    else:
        col = profit_cols[0]
        if not _sorted_descending([r.get(col) for r in rows]):
            failures.append(f"aggregated table '{name}' not sorted descending by {col}")

    return failures


def _check_dept_risk_flag_create_table(ctx: EvalRunContext) -> list[str]:
    failures: list[str] = []
    base = ctx.result_tables.get("部门预算")
    if base is None:
        return ["result missing 部门预算 table"]

    base_cols = _schema_keys(base)
    base_rows = base.get("rows", [])
    if "超预算风险" not in base_cols:
        failures.append("missing 超预算风险 column in 部门预算")
    else:
        bad = [r for r in base_rows if r.get("超预算风险") not in ("是", "否")]
        if bad:
            failures.append(f"{len(bad)} row(s) have 超预算风险 outside {{是,否}}")

    candidates = [
        name
        for name in (*ctx.new_tables, *ctx.result_tables.keys())
        if "风险" in name and name != "部门预算"
    ]
    if not candidates:
        failures.append("no new 高风险部门 table found")
    else:
        name = candidates[0]
        risk_table = ctx.result_tables.get(name)
        risk_rows = risk_table.get("rows", []) if risk_table else []
        if not risk_rows:
            failures.append(f"'{name}' is missing or empty")
        elif base_rows and len(risk_rows) > len(base_rows):
            failures.append(f"'{name}' has more rows than 部门预算")

    return failures


CASES: list[EvalCase] = [
    # 对应 test-data/test-prompts.md 单表场景 1
    EvalCase(
        id="sales_amount_filter_sort",
        title="销售订单：金额 + 过滤 + 排序",
        prompt=(
            "在`销售订单`工作表上生成一个清洗与分析计划：\n"
            "1）确保`数量`和`单价`被当作数值列（需要的话先转换列类型）；\n"
            "2）新增一列`金额`，表达式为`数量 * 单价`；\n"
            "3）只保留`订单日期`在 2024 年的行（用合适的条件过滤），其它行过滤掉；\n"
            "4）按`金额`从大到小排序整张表。\n"
            "只需要输出结构化 Plan JSON。"
        ),
        target_tables=("销售订单",),
        required_actions=frozenset({"add_column", "filter_rows", "sort_table"}),
        min_steps=3,
        check=_check_sales_amount_filter_sort,
    ),
    # 对应 test-data/test-prompts.md 单表场景 3
    EvalCase(
        id="dept_budget_usage_rate",
        title="部门预算：使用率与预算状态",
        prompt=(
            "在`部门预算`工作表中：\n"
            "1）新增一列`使用率`，表达式为`已使用 / 年度预算`；\n"
            "2）再新增一列`预算状态`，根据`使用率`分三档：≥0.8 为`紧张`，0.5~0.8 为`正常`，<0.5 为`宽松`；\n"
            "3）按`使用率`从高到低排序整张表。\n"
            "使用 add_column、sort_table 等 step 输出 Plan。"
        ),
        target_tables=("部门预算",),
        required_actions=frozenset({"add_column", "sort_table"}),
        min_steps=3,
        check=_check_dept_budget_usage_rate,
    ),
    # 对应 test-data/test-prompts.md 多表场景 4
    EvalCase(
        id="category_summary_multitable",
        title="订单 + 产品信息：补列 + 类别汇总",
        prompt=(
            "基于`销售订单`和`产品信息`两张表生成多表数据准备与汇总 Plan：\n"
            "1）从`产品信息`表 lookup 到`销售订单`表，按产品名称关联，在订单表中新增`类别`和`成本价`两列；\n"
            "2）在订单表中新增`金额`列（`数量 * 单价`）和`毛利`列（`数量 * (单价 - 成本价)`）；\n"
            "3）基于订单表创建一个新的结果表`按类别汇总`，按`类别`分组，统计每个类别的`总销量（数量求和）`、"
            "`总销售额（金额求和）`、`总毛利（毛利求和）`；\n"
            "4）对`按类别汇总`表按`总毛利`从高到低排序。\n"
            "请只使用 lookup_column、add_column、aggregate_table、sort_table 等已定义的 step。"
        ),
        target_tables=("销售订单", "产品信息"),
        required_actions=frozenset(
            {"lookup_column", "add_column", "aggregate_table", "sort_table"}
        ),
        min_steps=4,
        check=_check_category_summary_multitable,
    ),
    # 对应 test-data/test-prompts.md 多表场景 6
    EvalCase(
        id="dept_risk_flag_create_table",
        title="部门预算：标记超预算风险并排序",
        prompt=(
            "在`部门预算`表上生成一个 Plan：\n"
            "1）新增一列`使用率`=`已使用 / 年度预算`；\n"
            "2）新增一列`超预算风险`，当`使用率 >= 0.8`时值为`是`，否则为`否`；\n"
            "3）按`使用率`从高到低排序；\n"
            "4）再基于该表创建一个新表`高风险部门列表`，只包含`超预算风险 = 是`的部门。\n"
            "避免使用未定义的动作，使用 add_column、sort_table、filter_rows、create_table 等。"
        ),
        target_tables=("部门预算",),
        required_actions=frozenset({"add_column", "sort_table", "create_table"}),
        min_steps=4,
        check=_check_dept_risk_flag_create_table,
    ),
    # 模糊目标：多表 + 未点名表时不得 silent 出错 —— 要么澄清（gate 或模型主动），
    # 要么 plan 显式指定合法 table（Data profile 加持下模型自选表是合法行为，
    # preview lifecycle 提供反悔安全网）。gate 触发本身由 test_clarification.py
    # 等确定性单测守卫。
    EvalCase(
        id="ambiguous_add_column_no_silent_target",
        title="多表场景下未指定表的新增列请求：澄清或显式指定合法表",
        prompt="帮我新增一列'备注'，值先留空即可。",
        target_tables=("销售订单", "产品信息"),
        ambiguous_target=True,
    ),
    # 语义模糊（p1-clarify-dual-track）：两表都点名、无缺表/跨表列名冲突——post-plan
    # 规则轨（clarification.py）不会触发；但 join 键未指明，只能靠 pre-llm_decide 的
    # LLM 软扫描（clarify_scan.py）识别。双轨互斥/超时等确定性行为由
    # test_clarify_scan.py 守卫，本用例只做宽松的“不 silent 出错”质量信号。
    EvalCase(
        id="ambiguous_join_key_semantic_clarify",
        title="模糊 join 键：两表都点名但关联字段未指明，属语义歧义而非缺表",
        prompt="把'销售订单'和'产品信息'关联起来，加上产品类别这一列。",
        target_tables=("销售订单", "产品信息"),
        ambiguous_target=True,
    ),
]
