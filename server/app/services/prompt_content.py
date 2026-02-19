"""系统提示词正文：与消息构建逻辑分离，Schema 由 Pydantic Plan 动态注入。"""
import json

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


# 共用前缀：Schema 由上面 _PLAN_SCHEMA_JSON 注入
_SYSTEM_PREFIX = """You are an agent that edits a spreadsheet by generating an execution plan.

Output rules (VERY IMPORTANT):
- Output ONLY valid JSON.
- Do NOT include explanations, markdown, or code fences.
- Do NOT include any text outside the JSON.
- The JSON must strictly follow the schema below.
- If ambiguous, choose the simplest reasonable interpretation.

Schema:
"""

_SPREADSHEET_RULES = """

Rules:
- add_column.expression is a JavaScript expression evaluated as (row) => expression
- Use row.<columnName> to access values
- transform_column.replace args: {"from": string, "to": string}
- transform_column.parse_date args: {"formatHint"?: string}
"""

_PROJECT_RULES = """

Rules:
- "table" in add_column/transform_column: target table name; omit if only one table.
- add_column.expression: JavaScript (row) => expression; use row.<col> for values.
- transform_column.replace args: {"from": string, "to": string}; parse_date args: {"formatHint"?: string}
- join_tables: join left and right tables on leftKey/rightKey; resultTable is the new table name.
- create_table: create new table from source; expression is (rows)=>filtered rows, or omit for full copy.
"""

# 模块加载时生成，对外仍为常量
SPREADSHEET_SYSTEM = build_spreadsheet_system()
PROJECT_SYSTEM = build_project_system()
