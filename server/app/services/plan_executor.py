"""Plan 执行引擎：在后端对表数据执行 Plan（与前端 engine.ts 语义对齐）。

本模块提供 apply_plan / apply_project_plan，用于在后端执行 LLM 生成的
Plan（add_column / transform_column / join_tables / create_table）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Mapping, MutableMapping, Sequence

from app.models.plan import AggregationSpec, LookupColumnMapping, Plan, Step


TableName = str


@dataclass
class SchemaCol:
    """列定义的最小子集，用于执行阶段推断与传递类型信息。"""

    key: str
    type: Literal["number", "string", "date"] = "string"


@dataclass
class TableData:
    """执行阶段使用的表结构，尽量与前端 TableData 对齐。"""

    name: TableName
    rows: List[Dict[str, Any]]
    schema: List[SchemaCol]


@dataclass
class ApplyResult:
    """单表 Plan 执行结果。"""

    rows: List[Dict[str, Any]]
    schema: List[SchemaCol]
    diff: Dict[str, List[str]]  # {addedColumns: [], modifiedColumns: []}


@dataclass
class ProjectApplyResult:
    """多表 Plan 执行结果。"""

    tables: Dict[TableName, TableData]
    diff: Dict[str, List[str]]
    new_tables: List[TableName]


def _clone_rows(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """浅拷贝行列表，避免在原数据上就地修改。"""
    return [dict(r) for r in rows]


def _clone_schema(schema: Sequence[SchemaCol]) -> List[SchemaCol]:
    """浅拷贝 schema。"""
    return [SchemaCol(key=c.key, type=c.type) for c in schema]


def _infer_schema(rows: Sequence[Mapping[str, Any]]) -> List[SchemaCol]:
    """根据首行与非空值推断列名与类型（与前端 inferSchema 语义一致）。"""
    first = rows[0] if rows else {}
    keys = list(first.keys())
    out: List[SchemaCol] = []
    for k in keys:
        v = None
        for r in rows:
            if r.get(k) is not None:
                v = r.get(k)
                break
        if isinstance(v, (int, float)):
            t: Literal["number", "string", "date"] = "number"
        else:
            # 前端在 Date 实例时识别为 "date"，但后端从 JSON 进来通常已是 str。
            # 这里保持简单实现：非 number 一律 string。
            t = "string"
        out.append(SchemaCol(key=k, type=t))
    return out


def _sort_rows(
    rows: Sequence[Mapping[str, Any]],
    column: str,
    order: Literal["ascending", "descending"],
) -> List[Dict[str, Any]]:
    """按照指定列对行进行稳定排序，None 统一排在末尾。"""
    non_none_rows: List[Dict[str, Any]] = [
        dict(r) for r in rows if r.get(column) is not None
    ]
    none_rows: List[Dict[str, Any]] = [
        dict(r) for r in rows if r.get(column) is None
    ]
    non_none_rows.sort(key=lambda r: r.get(column))
    if order == "descending":
        non_none_rows.reverse()
    return non_none_rows + none_rows


def _safe_globals() -> Dict[str, Any]:
    """返回用于表达式执行的安全环境。"""
    # 仅暴露少量常用函数，避免滥用内置。
    allowed_builtins = {
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "round": round,
    }
    return {"__builtins__": {}, **allowed_builtins}


def _normalize_row_expression(expression: str) -> str:
    """若 expression 为完整箭头形式 (row) => body 或 row => body，则只返回 body。

    这样 lambda row: (body) 在 Python 中合法；且 body 中 row.列名 需配合
    将 row 转为支持属性访问的对象（如 SimpleNamespace）使用。
    """
    m = re.match(r"^\s*(?:\(row\)|row)\s*=>\s*([\s\S]+)$", expression)
    return m.group(1).strip() if m else expression


def _eval_row_expression(expression: str, row: Mapping[str, Any]) -> Any:
    """在受限环境下对单行执行表达式，失败时返回 None（与前端 safeEval 类似）。

    支持 LLM 返回的完整箭头 "row => row.列名..."；将 row 转为 SimpleNamespace
    以便 row.列名 形式的属性访问在 Python 中可用。
    """
    try:
        body = _normalize_row_expression(expression)
        fn = eval(f"lambda row: ({body})", _safe_globals(), {})
        row_obj = SimpleNamespace(**dict(row))
        return fn(row_obj)
    except Exception:
        return None


def _resolve_table_name(step: Step, table_names: List[TableName]) -> TableName:
    """与前端 resolveTable 语义一致：优先使用 step.table，否则取第一张表。"""
    table = getattr(step, "table", None)
    if table and table in table_names:
        return table
    return table_names[0] if table_names else ""


def apply_plan(
    rows: Sequence[Mapping[str, Any]],
    schema: Sequence[SchemaCol],
    plan: Plan,
) -> ApplyResult:
    """在单表上执行 Plan，返回新行、schema 与 diff。"""
    next_rows = _clone_rows(rows)
    next_schema = _clone_schema(schema)
    diff: Dict[str, List[str]] = {"addedColumns": [], "modifiedColumns": []}

    for step in plan.steps:
        if step.action == "add_column":
            name = step.name
            expr = step.expression
            next_rows = [
                {**r, name: _eval_row_expression(expr, r)} for r in next_rows
            ]
            if not any(c.key == name for c in next_schema):
                next_schema.append(SchemaCol(key=name, type="string"))
                diff["addedColumns"].append(name)
            else:
                diff["modifiedColumns"].append(name)

        if step.action == "transform_column":
            col = step.column
            kind = step.transform
            args = step.args or {}
            next_rows = [
                {**r, col: _transform_value(r.get(col), kind, args)} for r in next_rows
            ]
            if col not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(col)

        if step.action == "sort_table":
            col = step.column
            order = step.order
            next_rows = _sort_rows(next_rows, col, order)

        if step.action == "filter_rows":
            expr = step.condition
            next_rows = [
                r for r in next_rows if _eval_row_expression(expr, r)
            ]

        if step.action == "delete_rows":
            expr = step.condition
            next_rows = [
                r for r in next_rows if not _eval_row_expression(expr, r)
            ]

        if step.action == "deduplicate_rows":
            keep: Literal["first", "last"] = getattr(step, "keep", "first")
            next_rows = _deduplicate_rows(next_rows, step.keys, keep)

        if step.action == "rename_column":
            from_name = step.fromName
            to_name = step.toName
            updated_rows: List[Dict[str, Any]] = []
            for r in next_rows:
                if from_name not in r:
                    updated_rows.append(dict(r))
                    continue
                new_row = dict(r)
                new_row[to_name] = new_row.pop(from_name)
                updated_rows.append(new_row)
            next_rows = updated_rows
            for col in next_schema:
                if col.key == from_name:
                    col.key = to_name
            if to_name not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(to_name)

        if step.action == "fill_missing":
            col = step.column
            strategy = step.strategy
            fill_value = _compute_fill_value(next_rows, col, strategy, step.value)
            if fill_value is not None:
                updated_rows: List[Dict[str, Any]] = []
                for r in next_rows:
                    if r.get(col) is None:
                        new_row = dict(r)
                        new_row[col] = fill_value
                        updated_rows.append(new_row)
                    else:
                        updated_rows.append(dict(r))
                next_rows = updated_rows
                if col not in diff["modifiedColumns"]:
                    diff["modifiedColumns"].append(col)

        if step.action == "cast_column_type":
            col = step.column
            target = step.targetType
            next_rows = [
                {**r, col: _cast_value(r.get(col), target)} for r in next_rows
            ]
            for c in next_schema:
                if c.key == col:
                    c.type = target  # type: ignore[assignment]
            if col not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(col)

        if step.action == "delete_column":
            col = step.column
            updated_rows: List[Dict[str, Any]] = []
            for r in next_rows:
                if col not in r:
                    updated_rows.append(dict(r))
                    continue
                new_row = dict(r)
                new_row.pop(col, None)
                updated_rows.append(new_row)
            next_rows = updated_rows
            next_schema = [c for c in next_schema if c.key != col]
            if col not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(col)

        if step.action == "reorder_columns":
            specified = step.columns
            existing_keys = [c.key for c in next_schema]
            ordered_existing = [k for k in existing_keys if k in specified]
            remaining = [k for k in existing_keys if k not in specified]
            new_order = ordered_existing + remaining
            updated_rows = []
            for r in next_rows:
                new_row: Dict[str, Any] = {k: r.get(k) for k in new_order}
                updated_rows.append(new_row)
            next_rows = updated_rows
            key_to_col = {c.key: c for c in next_schema}
            next_schema = [key_to_col[k] for k in new_order if k in key_to_col]

    return ApplyResult(rows=next_rows, schema=next_schema, diff=diff)


def apply_project_plan(
    tables: Mapping[TableName, TableData],
    plan: Plan,
) -> ProjectApplyResult:
    """在多表项目上执行 Plan，支持行级、列级与多表操作。"""
    next_tables: Dict[TableName, TableData] = {
        name: TableData(
            name=name,
            rows=_clone_rows(t.rows),
            schema=_clone_schema(t.schema),
        )
        for name, t in tables.items()
    }
    diff: Dict[str, List[str]] = {"addedColumns": [], "modifiedColumns": []}
    new_tables: List[TableName] = []
    table_names: List[TableName] = list(tables.keys())

    for step in plan.steps:
        if step.action == "add_column":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            name = step.name
            expr = step.expression
            t.rows = [{**r, name: _eval_row_expression(expr, r)} for r in t.rows]
            if not any(c.key == name for c in t.schema):
                t.schema.append(SchemaCol(key=name, type="string"))
                diff["addedColumns"].append(name)
            else:
                diff["modifiedColumns"].append(name)

        if step.action == "transform_column":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            col = step.column
            kind = step.transform
            args = step.args or {}
            t.rows = [
                {**r, col: _transform_value(r.get(col), kind, args)} for r in t.rows
            ]
            if col not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(col)

        if step.action == "sort_table":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            t.rows = _sort_rows(t.rows, step.column, step.order)

        if step.action == "filter_rows":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            expr = step.condition
            t.rows = [r for r in t.rows if _eval_row_expression(expr, r)]

        if step.action == "delete_rows":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            expr = step.condition
            t.rows = [r for r in t.rows if not _eval_row_expression(expr, r)]

        if step.action == "deduplicate_rows":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            keep: Literal["first", "last"] = getattr(step, "keep", "first")
            t.rows = _deduplicate_rows(t.rows, step.keys, keep)

        if step.action == "rename_column":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            from_name = step.fromName
            to_name = step.toName
            updated_rows: List[Dict[str, Any]] = []
            for r in t.rows:
                if from_name not in r:
                    updated_rows.append(dict(r))
                    continue
                new_row = dict(r)
                new_row[to_name] = new_row.pop(from_name)
                updated_rows.append(new_row)
            t.rows = updated_rows
            for col in t.schema:
                if col.key == from_name:
                    col.key = to_name
            if to_name not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(to_name)

        if step.action == "fill_missing":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            col = step.column
            strategy = step.strategy
            fill_value = _compute_fill_value(t.rows, col, strategy, step.value)
            if fill_value is not None:
                updated_rows: List[Dict[str, Any]] = []
                for r in t.rows:
                    if r.get(col) is None:
                        new_row = dict(r)
                        new_row[col] = fill_value
                        updated_rows.append(new_row)
                    else:
                        updated_rows.append(dict(r))
                t.rows = updated_rows
                if col not in diff["modifiedColumns"]:
                    diff["modifiedColumns"].append(col)

        if step.action == "cast_column_type":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            col = step.column
            target = step.targetType
            t.rows = [
                {**r, col: _cast_value(r.get(col), target)} for r in t.rows
            ]
            for c in t.schema:
                if c.key == col:
                    c.type = target  # type: ignore[assignment]
            if col not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(col)

        if step.action == "delete_column":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            col = step.column
            updated_rows: List[Dict[str, Any]] = []
            for r in t.rows:
                if col not in r:
                    updated_rows.append(dict(r))
                    continue
                new_row = dict(r)
                new_row.pop(col, None)
                updated_rows.append(new_row)
            t.rows = updated_rows
            t.schema = [c for c in t.schema if c.key != col]
            if col not in diff["modifiedColumns"]:
                diff["modifiedColumns"].append(col)

        if step.action == "reorder_columns":
            tn = _resolve_table_name(step, table_names)
            t = next_tables.get(tn)
            if not t:
                continue
            specified = step.columns
            existing_keys = [c.key for c in t.schema]
            ordered_existing = [k for k in existing_keys if k in specified]
            remaining = [k for k in existing_keys if k not in specified]
            new_order = ordered_existing + remaining
            updated_rows: List[Dict[str, Any]] = []
            for r in t.rows:
                new_row: Dict[str, Any] = {k: r.get(k) for k in new_order}
                updated_rows.append(new_row)
            t.rows = updated_rows
            key_to_col = {c.key: c for c in t.schema}
            t.schema = [key_to_col[k] for k in new_order if k in key_to_col]

        if step.action == "join_tables":
            left_t = next_tables.get(step.left) or tables.get(step.left)
            right_t = next_tables.get(step.right) or tables.get(step.right)
            if not left_t or not right_t:
                continue
            join_type = step.joinType or "inner"
            rows = _do_join(
                left_t.rows, right_t.rows, step.leftKey, step.rightKey, join_type
            )
            schema = _infer_schema(rows)
            result_name = step.resultTable
            next_tables[result_name] = TableData(
                name=result_name, rows=rows, schema=schema
            )
            new_tables.append(result_name)
            table_names.append(result_name)

        if step.action == "create_table":
            src = next_tables.get(step.source) or tables.get(step.source)
            if not src:
                continue
            rows = _clone_rows(src.rows)
            if step.expression:
                try:
                    fn = eval(
                        f"lambda rows: ({step.expression})",
                        _safe_globals(),
                        {},
                    )
                    result = fn(rows)
                    if isinstance(result, list):
                        rows = [dict(r) for r in result]  # 保底转换为 dict 列表
                except Exception:
                    # 表达式失败时保留原 rows
                    pass
            schema = _infer_schema(rows)
            next_tables[step.name] = TableData(
                name=step.name, rows=rows, schema=schema
            )
            new_tables.append(step.name)
            table_names.append(step.name)

        if step.action == "aggregate_table":
            src = next_tables.get(step.source) or tables.get(step.source)
            if not src:
                continue
            rows = _aggregate_table(src.rows, step.groupBy, step.aggregations)
            schema = _infer_schema(rows)
            result_name = step.resultTable
            next_tables[result_name] = TableData(
                name=result_name, rows=rows, schema=schema
            )
            new_tables.append(result_name)
            table_names.append(result_name)

        if step.action == "union_tables":
            rows = _union_tables(
                next_tables=next_tables,
                original_tables=tables,
                sources=step.sources,
                mode=step.mode or "relaxed",
            )
            schema = _infer_schema(rows)
            result_name = step.resultTable
            next_tables[result_name] = TableData(
                name=result_name, rows=rows, schema=schema
            )
            new_tables.append(result_name)
            table_names.append(result_name)

        if step.action == "lookup_column":
            main = next_tables.get(step.mainTable) or tables.get(step.mainTable)
            lookup = next_tables.get(step.lookupTable) or tables.get(
                step.lookupTable
            )
            if not main or not lookup:
                continue
            updated_rows, updated_schema, added_cols, modified_cols = _apply_lookup(
                main.rows,
                main.schema,
                lookup.rows,
                step.mainKey,
                step.lookupKey,
                step.columns,
            )
            next_tables[step.mainTable] = TableData(
                name=main.name, rows=updated_rows, schema=updated_schema
            )
            for col in added_cols:
                if col not in diff["addedColumns"]:
                    diff["addedColumns"].append(col)
            for col in modified_cols:
                if col not in diff["modifiedColumns"]:
                    diff["modifiedColumns"].append(col)

    return ProjectApplyResult(tables=next_tables, diff=diff, new_tables=new_tables)


def _do_join(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    left_key: str,
    right_key: str,
    join_type: Literal["inner", "left", "right"],
) -> List[Dict[str, Any]]:
    """与前端 doJoin 语义一致的简单 join 实现。"""
    right_by_key: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in right:
        k = r.get(right_key)
        right_by_key.setdefault(k, []).append(r)

    left_by_key: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in left:
        k = r.get(left_key)
        left_by_key.setdefault(k, []).append(r)

    result: List[Dict[str, Any]] = []

    if join_type == "right":
        for r in right:
            k = r.get(right_key)
            matches = left_by_key.get(k) or []
            if not matches:
                result.append(
                    {**_empty_with_left_schema(left), **_prefix_keys(r, "right_")}
                )
            else:
                for m in matches:
                    result.append({**dict(m), **_prefix_keys(r, "right_")})
        return result

    for row in left:
        k = row.get(left_key)
        matches = right_by_key.get(k) or []
        if not matches and join_type == "inner":
            continue
        if not matches and join_type == "left":
            result.append({**dict(row), **_empty_with_right_schema(right)})
        else:
            for m in matches:
                result.append({**dict(row), **_prefix_keys(m, "right_")})
    return result


def _prefix_keys(obj: Mapping[str, Any], prefix: str) -> Dict[str, Any]:
    """为对象键添加前缀。"""
    return {f"{prefix}{k}": v for k, v in obj.items()}


def _empty_with_right_schema(right: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """根据右表首行生成全为 None 的占位列（带 right_ 前缀）。"""
    sample = right[0] if right else {}
    return {f"right_{k}": None for k in sample.keys()}


def _empty_with_left_schema(left: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """根据左表首行生成全为 None 的占位列。"""
    sample = left[0] if left else {}
    return {k: None for k in sample.keys()}


def _transform_value(
    v: Any,
    kind: str,
    args: MutableMapping[str, Any] | Mapping[str, Any],
) -> Any:
    """与前端 transformValue 对齐的字符串变换。"""
    if v is None:
        return v
    s = str(v)

    if kind == "trim":
        return s.strip()
    if kind == "lower":
        return s.lower()
    if kind == "upper":
        return s.upper()
    if kind == "replace":
        from_ = str(args.get("from", ""))
        to = str(args.get("to", ""))
        return s.replace(from_, to)
    if kind == "parse_date":
        # 简化实现：尝试按 ISO 日期解析，否则返回原值
        from datetime import datetime

        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                d = datetime.strptime(s, fmt)
                return d.date().isoformat()
            except ValueError:
                continue
        return v
    return v


def _deduplicate_rows(
    rows: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
    keep: Literal["first", "last"],
) -> List[Dict[str, Any]]:
    """按指定键去重，支持 first/last 策略。"""
    if not keys:
        return [dict(r) for r in rows]
    seen: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = repr([r.get(k) for k in keys])
        if keep == "first":
            seen.setdefault(key, dict(r))
        else:
            seen[key] = dict(r)
    return list(seen.values())


def _compute_fill_value(
    rows: Sequence[Mapping[str, Any]],
    column: str,
    strategy: str,
    value: Any | None,
) -> Any | None:
    """计算填充缺失值所需的聚合值。"""
    values: List[Any] = [r.get(column) for r in rows if r.get(column) is not None]
    if strategy == "constant":
        return value
    if not values:
        return None
    if strategy in ("mean", "median"):
        nums: List[float] = []
        for v in values:
            try:
                n = float(v)
            except (TypeError, ValueError):
                continue
            nums.append(n)
        if not nums:
            return None
        if strategy == "mean":
            return sum(nums) / len(nums)
        nums_sorted = sorted(nums)
        mid = len(nums_sorted) // 2
        if len(nums_sorted) % 2 == 0:
            return (nums_sorted[mid - 1] + nums_sorted[mid]) / 2
        return nums_sorted[mid]
    if strategy == "mode":
        freq: Dict[Any, int] = {}
        for v in values:
            freq[v] = freq.get(v, 0) + 1
        best = None
        best_count = -1
        for v, c in freq.items():
            if c > best_count:
                best = v
                best_count = c
        return best
    return None


def _cast_value(
    v: Any,
    target: Literal["number", "string", "date"],
) -> Any:
    """简单类型转换，与前端 castValue 对齐。"""
    if v is None:
        return v
    if target == "string":
        return str(v)
    if target == "number":
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return n
    if target == "date":
        from datetime import datetime

        s = str(v)
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                d = datetime.strptime(s, fmt)
                return d.date().isoformat()
            except ValueError:
                continue
        return v
    return v


def _aggregate_table(
    rows: Sequence[Mapping[str, Any]],
    group_by: Sequence[str],
    aggregations: Sequence[AggregationSpec],
) -> List[Dict[str, Any]]:
    """按照 group_by + aggregations 进行聚合，返回新行列表。"""
    if not group_by:
        return []
    groups: Dict[str, Dict[str, Any]] = {}
    rows_by_group: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        key_vals = [r.get(k) for k in group_by]
        key = repr(key_vals)
        if key not in groups:
            groups[key] = {name: val for name, val in zip(group_by, key_vals)}
            rows_by_group[key] = []
        rows_by_group[key].append(r)

    result: List[Dict[str, Any]] = []
    for key, base in groups.items():
        grp_rows = rows_by_group[key]
        out = dict(base)
        for agg in aggregations:
            vals = [r.get(agg.column) for r in grp_rows if r.get(agg.column) is not None]
            value: Any
            if agg.op == "count":
                value = len(vals)
            else:
                nums: List[float] = []
                for v in vals:
                    try:
                        n = float(v)
                    except (TypeError, ValueError):
                        continue
                    nums.append(n)
                if not nums:
                    value = None
                elif agg.op == "sum":
                    value = sum(nums)
                elif agg.op == "avg":
                    value = sum(nums) / len(nums)
                elif agg.op == "max":
                    value = max(nums)
                elif agg.op == "min":
                    value = min(nums)
                else:
                    value = None
            out[agg.as_] = value
        result.append(out)
    return result


def _union_tables(
    next_tables: Mapping[TableName, TableData],
    original_tables: Mapping[TableName, TableData],
    sources: Sequence[TableName],
    mode: Literal["strict", "relaxed"],
) -> List[Dict[str, Any]]:
    """按 strict/relaxed 策略纵向合并多张表。"""
    tables: List[TableData] = []
    for name in sources:
        t = next_tables.get(name) or original_tables.get(name)
        if t:
            tables.append(t)
    if not tables:
        return []

    if mode == "strict":
        common_keys: Optional[set[str]] = None
        for t in tables:
            keys = {c.key for c in t.schema}
            if common_keys is None:
                common_keys = set(keys)
            else:
                common_keys &= keys
        keys_list = list(common_keys or [])
        rows: List[Dict[str, Any]] = []
        for t in tables:
            for r in t.rows:
                new_row = {k: r.get(k) for k in keys_list}
                rows.append(new_row)
        return rows

    all_keys: set[str] = set()
    for t in tables:
        for c in t.schema:
            all_keys.add(c.key)
    keys_list = list(all_keys)
    rows: List[Dict[str, Any]] = []
    for t in tables:
        for r in t.rows:
            new_row = {k: r.get(k) for k in keys_list}
            rows.append(new_row)
    return rows


def _apply_lookup(
    main_rows: Sequence[Mapping[str, Any]],
    main_schema: Sequence[SchemaCol],
    lookup_rows: Sequence[Mapping[str, Any]],
    main_key: str,
    lookup_key: str,
    columns: Sequence[LookupColumnMapping],
) -> tuple[List[Dict[str, Any]], List[SchemaCol], List[str], List[str]]:
    """在主表上执行列级 lookup，返回新行、schema 与列 diff。"""
    by_key: Dict[Any, Mapping[str, Any]] = {}
    for r in lookup_rows:
        by_key.setdefault(r.get(lookup_key), r)

    added_cols: List[str] = []
    modified_cols: List[str] = []
    new_rows: List[Dict[str, Any]] = []

    for r in main_rows:
        key = r.get(main_key)
        match = by_key.get(key)
        new_row = dict(r)
        for col in columns:
            to = col.to
            value = match.get(col.from_) if match else None
            if to in new_row and to not in modified_cols:
                modified_cols.append(to)
            if to not in new_row and to not in added_cols:
                added_cols.append(to)
            new_row[to] = value
        new_rows.append(new_row)

    schema = _clone_schema(main_schema)
    existing = {c.key for c in schema}
    for col in added_cols:
        if col not in existing:
            schema.append(SchemaCol(key=col, type="string"))

    return new_rows, schema, added_cols, modified_cols

