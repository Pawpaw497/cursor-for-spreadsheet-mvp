"""Agent 可调用的工具：读表、样本、列统计、校验表达式。"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.agent.state import TableContext
from app.services.plan_executor import _safe_globals

# 工具名与实现函数的注册表；(tables, **kwargs) -> str
_TOOL_IMPLS: Dict[str, Any] = {}


def _register(name: str):
    def deco(f):
        _TOOL_IMPLS[name] = f
        return f
    return deco


@_register("get_schema")
def get_schema(
    tables: List[TableContext],
    table_name: str | None = None,
) -> str:
    """
    返回指定表或全部表的 schema（列名与类型）。
    table_name 为空时：单表返回该表 schema，多表返回所有表的 schema。
    """
    if table_name:
        t = next((x for x in tables if x.name == table_name), None)
        if not t:
            return json.dumps({"error": f"Table not found: {table_name!r}"})
        return json.dumps(t.schema, ensure_ascii=False, indent=2)
    if len(tables) == 1:
        return json.dumps(tables[0].schema, ensure_ascii=False, indent=2)
    out = {t.name: t.schema for t in tables}
    return json.dumps(out, ensure_ascii=False, indent=2)


@_register("get_sample_rows")
def get_sample_rows(
    tables: List[TableContext],
    table_name: str | None = None,
    n: int = 5,
) -> str:
    """
    返回指定表或第一张表的前 n 行样本。
    table_name 为空时取第一张表；n 默认 5。
    """
    t = tables[0] if not table_name else next(
        (x for x in tables if x.name == table_name), None
    )
    if not t:
        return json.dumps({"error": f"Table not found: {table_name!r}"})
    n = max(0, min(n, 50))
    rows = t.sample_rows[:n]
    return json.dumps(rows, ensure_ascii=False, indent=2)


@_register("get_column_stats")
def get_column_stats(
    tables: List[TableContext],
    table_name: str,
    column: str,
) -> str:
    """
    基于样本行计算列的简单统计：非空数量、唯一数、最小/最大（若可比较）。
    """
    t = next((x for x in tables if x.name == table_name), None)
    if not t:
        return json.dumps({"error": f"Table not found: {table_name!r}"})
    if not t.sample_rows:
        return json.dumps({"count": 0, "distinct": 0})
    values = [
        r.get(column) for r in t.sample_rows if r.get(column) is not None
    ]
    count = len(values)
    distinct = len(set(str(v) for v in values))
    result: Dict[str, Any] = {"count": count, "distinct": distinct}
    try:
        comparable = [v for v in values if isinstance(v, (int, float))]
        if comparable:
            result["min"] = min(comparable)
            result["max"] = max(comparable)
    except TypeError:
        pass
    return json.dumps(result, ensure_ascii=False, indent=2)


@_register("validate_expression")
def validate_expression(
    tables: List[TableContext],
    expression: str,
    table_name: str | None = None,
) -> str:
    """
    用第一行样本在浏览器同构的 (row) => expr 下校验表达式是否可执行。
    返回 ok 或错误信息。
    """
    t = tables[0] if not table_name else next(
        (x for x in tables if x.name == table_name), None
    )
    if not t or not t.sample_rows:
        return json.dumps({"ok": False, "error": "No sample row"})
    row = t.sample_rows[0]
    try:
        # 与前端 engine 一致： (row) => expression
        fn = eval(f"lambda row: ({expression})", _safe_globals(), {})
        fn(row)
        return json.dumps({"ok": True})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@_register("execute_step")
def execute_step(
    tables: List[TableContext],
    step: Dict[str, Any],
    table_name: str | None = None,
) -> str:
    """
    分步执行（demo 版）：目前仅返回 echo 信息，实际执行仍在前端 engine 中完成。
    主要用于让 Agent 在需要时显式调用“执行一步”这一语义。
    """
    return json.dumps(
        {
            "ok": True,
            "note": (
                "execute_step is a server-side stub; actual data mutation "
                "still happens in the frontend engine."
            ),
            "step": step,
            "table": table_name,
        },
        ensure_ascii=False,
        indent=2,
    )


@_register("rollback_last_step")
def rollback_last_step(
    tables: List[TableContext],
) -> str:
    """
    回滚上一步（demo 版）：当前表格状态仍完全由前端维护，这里只提供语义占位。
    """
    return json.dumps(
        {
            "ok": True,
            "note": (
                "rollback_last_step is a semantic hook for future server-side "
                "state; current demo rollback is handled in the frontend."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def run_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    tables: List[TableContext],
) -> str:
    """执行指定工具，返回 JSON 字符串结果。工具不存在或参数错误时返回错误 JSON。"""
    if tool_name not in _TOOL_IMPLS:
        return json.dumps({"error": f"Unknown tool: {tool_name!r}"})
    try:
        return _TOOL_IMPLS[tool_name](tables, **tool_args)
    except TypeError as e:
        return json.dumps({"error": f"Invalid arguments: {e}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_tools_spec_for_llm() -> List[Dict[str, Any]]:
    """返回供 OpenRouter/Ollama 使用的 tools 定义（OpenAI 兼容格式）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_schema",
                "description": (
                    "Get schema (column names and types) of a table or all "
                    "tables. Use when you need to know column names or types."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": (
                                "Table name; omit to get the only table or "
                                "all tables."
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_sample_rows",
                "description": "Get sample rows from a table to inspect data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "Table name; omit for first table.",
                        },
                        "n": {
                            "type": "integer",
                            "description": "Number of rows (default 5, max 50).",
                            "default": 5,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_column_stats",
                "description": (
                    "Get simple stats for a column (count, distinct, min/max "
                    "if numeric) from sample data."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "Table name.",
                        },
                        "column": {
                            "type": "string",
                            "description": "Column name.",
                        },
                    },
                    "required": ["table_name", "column"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "validate_expression",
                "description": (
                    "Validate a JavaScript-like expression (e.g. for "
                    "add_column) against the first sample row. (row) => <expr>."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": (
                                "Expression to evaluate, e.g. "
                                "row.price * row.quantity."
                            ),
                        },
                        "table_name": {
                            "type": "string",
                            "description": "Table name; omit for first table.",
                        },
                    },
                    "required": ["expression"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_step",
                "description": (
                    "Execute a single plan step (demo stub). Use when you want "
                    "to reason about step-wise execution; actual data mutation "
                    "still happens in the frontend."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "step": {
                            "type": "object",
                            "description": "A single plan step object.",
                        },
                        "table_name": {
                            "type": "string",
                            "description": "Target table name (optional).",
                        },
                    },
                    "required": ["step"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rollback_last_step",
                "description": (
                    "Rollback last executed step (demo stub). Currently only a "
                    "semantic hook; real rollback is handled in the frontend."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    ]
