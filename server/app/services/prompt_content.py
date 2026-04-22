"""系统提示词正文：与消息构建逻辑分离，Schema 由 Pydantic Plan 动态注入。"""
import json
from typing import Any, Dict, List, Optional

from app.models.plan import Plan

# 从 Plan 模型生成 JSON Schema，供注入到 system prompt
_PLAN_SCHEMA_JSON: str = json.dumps(
    Plan.model_json_schema(),
    indent=2,
    ensure_ascii=False,
)


def build_spreadsheet_system() -> str:
    """单表场景：使用 Plan 的 JSON Schema 动态生成 system prompt。"""
    return _SYSTEM_PREFIX + _PLAN_SCHEMA_JSON + _SPREADSHEET_RULES


def build_project_system() -> str:
    """多表场景：使用 Plan 的 JSON Schema 动态生成 system prompt。"""
    return _SYSTEM_PREFIX + _PLAN_SCHEMA_JSON + _PROJECT_RULES


def build_column_stats_text(
    sample_rows: List[Dict[str, Any]],
    column_keys: Optional[List[str]] = None,
) -> str:
    """根据样本行生成列级统计摘要，供 user 消息使用以降低盲目规划风险。

    @param sample_rows: 当前表（或表之一）的样本行。
    @param column_keys: 要统计的列名；默认取首行键或 schema 中的 key 列表。
    @return: 可读的统计文本，无数据时返回空串。
    """
    if not sample_rows:
        return ""
    if not column_keys:
        column_keys = list(sample_rows[0].keys())
    n = len(sample_rows)
    lines: List[str] = ["Column statistics (from sample rows above):"]
    for k in column_keys:
        vals = [r.get(k) for r in sample_rows]
        nulls = sum(1 for v in vals if v is None)
        non_null = [v for v in vals if v is not None]
        distinct = len({repr(x) for x in non_null})
        ratio = (nulls / n) if n else 0.0
        line = (
            f"  - {k}: n={n}, nulls={nulls}, null_ratio={ratio:.2f}, "
            f"distinct_non_null={distinct}"
        )
        nums: List[float] = []
        for v in non_null:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                nums.append(float(v))
            else:
                try:
                    nums.append(float(v))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
        if nums:
            mean_v = sum(nums) / len(nums)
            line += f", min={min(nums):.4g}, max={max(nums):.4g}, mean={mean_v:.4g}"
        lines.append(line)
    return "\n".join(lines) + "\n"


# 共用前缀：Schema 由上面 _PLAN_SCHEMA_JSON 注入
_SYSTEM_PREFIX = (
    "You are an agent that edits a spreadsheet by generating an execution plan.\n\n"
    "Output rules (VERY IMPORTANT):\n"
    "- Output ONLY valid JSON.\n"
    "- Do NOT include explanations, markdown, or code fences.\n"
    "- Do NOT include any text outside the JSON.\n"
    "- The JSON must strictly follow the schema below.\n"
    "- If ambiguous, choose the simplest reasonable interpretation.\n\n"
    "Schema:\n"
)

_SPREADSHEET_RULES = (
    "\n\nRules:\n"
    "- add_column.expression is a JavaScript expression evaluated as "
    "(row) => expression\n"
    "- Use row.<columnName> to access values\n"
    '- transform_column.replace args: {"from": string, "to": string}\n'
    '- transform_column.parse_date args: {"formatHint"?: string}\n'
    "- sort_table: "
    '{"action":"sort_table","column": string,'
    '"order":"ascending"|"descending"}; '
    "only changes row order, not values.\n"
    "- filter_rows / delete_rows: condition is the same row expression form as add_column; "
    "filter_rows keeps matching rows, delete_rows removes matching rows.\n"
    "- deduplicate_rows: keys list which columns define uniqueness; keep first|last.\n"
    "- fill_missing: strategy constant|mean|median|mode; value used when strategy=constant.\n"
    "- cast_column_type: targetType number|string|date.\n"
    "- delete_column: remove a column; reorder_columns: partial order, rest append.\n"
    "- validate_table: rules are row expressions; no data change; use level warn|error.\n"
    "- pivot_table: index=row id columns, columns=column to spread, values=cell values, "
    "resultTable=new wide table. unpivot_table: idVars + valueVars to long format.\n"
    "- For multi-table output use join_tables, create_table, aggregate_table, "
    "pivot_table, union_tables, or lookup_column as in schema.\n"
)


_PROJECT_RULES = (
    "\n\nRules:\n"
    '- "table" in steps that have optional table: target table name; '
    "omit if only one table (add_column, transform_column, sort_table, etc.).\n"
    "- add_column.expression: JavaScript (row) => expression; "
    "use row.<col> for values.\n"
    "- transform_column.replace args: "
    '{"from": string, "to": string}; '
    'parse_date args: {"formatHint"?: string}\n'
    "- join_tables: join left and right on leftKey/rightKey; resultTable is the new name.\n"
    "- create_table: from source; expression optional (rows)=>filtered rows.\n"
    "- sort_table: only changes row order in the target table.\n"
    "- filter_rows / delete_rows / deduplicate_rows: same as single-table rules; "
    "set table when multiple tables exist.\n"
    "- aggregate_table: groupBy + aggregations with op sum|avg|count|max|min; "
    "resultTable is new table.\n"
    "- union_tables: sources list; mode strict=common columns only, relaxed=union all keys.\n"
    "- lookup_column: VLOOKUP-style from lookupTable to mainTable on key columns.\n"
    "- delete_column / reorder_columns: structural changes on one table.\n"
    "- validate_table: list of row-level checks; does not change data.\n"
    "- pivot_table / unpivot_table: resultTable must be a new name; source is the input table name.\n"
)

# 模块加载时生成，对外仍为常量
SPREADSHEET_SYSTEM = build_spreadsheet_system()
PROJECT_SYSTEM = build_project_system()

