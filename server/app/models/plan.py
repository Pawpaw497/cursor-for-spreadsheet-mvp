"""Plan 相关 Schema。"""
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

TransformKind = Literal["trim", "lower", "upper", "replace", "parse_date"]


class AddColumnStep(BaseModel):
    action: Literal["add_column"]
    name: str
    expression: str
    table: Optional[str] = None
    note: Optional[str] = None


class TransformColumnStep(BaseModel):
    action: Literal["transform_column"]
    column: str
    transform: TransformKind
    args: Optional[Dict[str, Any]] = None
    table: Optional[str] = None
    note: Optional[str] = None


class SortTableStep(BaseModel):
    action: Literal["sort_table"]
    table: Optional[str] = None
    column: str
    order: Literal["ascending", "descending"] = "ascending"
    note: Optional[str] = None


class FilterRowsStep(BaseModel):
    action: Literal["filter_rows"]
    condition: str
    table: Optional[str] = None
    note: Optional[str] = None


class DeleteRowsStep(BaseModel):
    action: Literal["delete_rows"]
    condition: str
    table: Optional[str] = None
    note: Optional[str] = None


class DeduplicateRowsStep(BaseModel):
    action: Literal["deduplicate_rows"]
    keys: List[str]
    keep: Literal["first", "last"] = "first"
    table: Optional[str] = None
    note: Optional[str] = None


class RenameColumnStep(BaseModel):
    action: Literal["rename_column"]
    fromName: str
    toName: str
    table: Optional[str] = None
    note: Optional[str] = None


class FillMissingStep(BaseModel):
    action: Literal["fill_missing"]
    column: str
    strategy: Literal["constant", "mean", "median", "mode"]
    value: Optional[Any] = None
    table: Optional[str] = None
    note: Optional[str] = None


class CastColumnTypeStep(BaseModel):
    action: Literal["cast_column_type"]
    column: str
    targetType: Literal["number", "string", "date"]
    table: Optional[str] = None
    note: Optional[str] = None


class JoinTablesStep(BaseModel):
    action: Literal["join_tables"]
    left: str
    right: str
    leftKey: str
    rightKey: str
    resultTable: str
    joinType: Literal["inner", "left", "right"] = "inner"
    note: Optional[str] = None


class CreateTableStep(BaseModel):
    action: Literal["create_table"]
    name: str
    source: str
    expression: Optional[str] = None
    note: Optional[str] = None


class AggregationSpec(BaseModel):
    """聚合配置，使用 alias \"as\" 暴露字段名。"""

    model_config = ConfigDict(populate_by_name=True)

    column: str
    op: Literal["sum", "avg", "count", "max", "min"]
    as_: str = Field(alias="as")


class AggregateTableStep(BaseModel):
    action: Literal["aggregate_table"]
    source: str
    groupBy: List[str]
    aggregations: List[AggregationSpec]
    resultTable: str
    note: Optional[str] = None


class UnionTablesStep(BaseModel):
    action: Literal["union_tables"]
    sources: List[str]
    resultTable: str
    mode: Literal["strict", "relaxed"] = "relaxed"
    note: Optional[str] = None


class LookupColumnMapping(BaseModel):
    """lookup 列配置：from -> to。"""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class LookupColumnStep(BaseModel):
    action: Literal["lookup_column"]
    mainTable: str
    lookupTable: str
    mainKey: str
    lookupKey: str
    columns: List[LookupColumnMapping]
    note: Optional[str] = None


class DeleteColumnStep(BaseModel):
    action: Literal["delete_column"]
    column: str
    table: Optional[str] = None
    note: Optional[str] = None


class ReorderColumnsStep(BaseModel):
    action: Literal["reorder_columns"]
    columns: List[str]
    table: Optional[str] = None
    note: Optional[str] = None


class ValidateTableStep(BaseModel):
    """对表数据做行级规则校验，不修改数据，仅向 diff 写入告警或错误。"""

    action: Literal["validate_table"]
    table: Optional[str] = None
    rules: List[str]
    level: Literal["warn", "error"] = "warn"
    note: Optional[str] = None


class PivotTableStep(BaseModel):
    """按 index 与 pivot 列将 values 列透视到新表。"""

    action: Literal["pivot_table"]
    source: str
    index: List[str]
    columns: str
    values: str
    agg: Literal["sum", "count", "avg", "max", "min"] = "sum"
    resultTable: str
    note: Optional[str] = None


class UnpivotTableStep(BaseModel):
    """将宽表多列压成长表（melt / unpivot）。"""

    action: Literal["unpivot_table"]
    source: str
    idVars: List[str]
    valueVars: List[str]
    varName: str = "variable"
    valueName: str = "value"
    resultTable: str
    note: Optional[str] = None


Step = Union[
    AddColumnStep,
    TransformColumnStep,
    SortTableStep,
    FilterRowsStep,
    DeleteRowsStep,
    DeduplicateRowsStep,
    RenameColumnStep,
    FillMissingStep,
    CastColumnTypeStep,
    JoinTablesStep,
    CreateTableStep,
    AggregateTableStep,
    UnionTablesStep,
    LookupColumnStep,
    DeleteColumnStep,
    ReorderColumnsStep,
    ValidateTableStep,
    PivotTableStep,
    UnpivotTableStep,
]


class Plan(BaseModel):
    intent: str
    steps: List[Step] = Field(min_length=1)


class TableInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    schema_: List[Dict[str, Any]] = Field(alias="schema")
    sampleRows: List[Dict[str, Any]]


class PlanRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    prompt: str
    schema_: List[Dict[str, Any]] = Field(alias="schema")
    sampleRows: List[Dict[str, Any]]
    modelSource: Optional[Literal["cloud", "local"]] = "cloud"
    cloudModelId: Optional[str] = None
    localModelId: Optional[str] = None


class ProjectPlanRequest(BaseModel):
    prompt: str
    tables: List[TableInfo] = Field(min_length=1)
    modelSource: Optional[Literal["cloud", "local"]] = "cloud"
    cloudModelId: Optional[str] = None
    localModelId: Optional[str] = None


class PlanResponse(BaseModel):
    plan: Plan


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentProjectPlanRequest(ProjectPlanRequest):
    """带历史与已应用计划摘要的 Agent 请求。"""

    history: List[ConversationTurn] = Field(default_factory=list)
    appliedPlansSummary: Optional[str] = None


class ProjectPlanByIdRequest(BaseModel):
    """基于后端 ProjectState 的项目级 Plan 请求，仅携带 prompt 与模型信息。"""

    prompt: str
    modelSource: Optional[Literal["cloud", "local"]] = "cloud"
    cloudModelId: Optional[str] = None
    localModelId: Optional[str] = None


class ExecuteProjectPlanRequest(BaseModel):
    """基于后端 ProjectState 执行 Plan 的请求。"""

    plan: Plan


class ExecuteTable(BaseModel):
    """执行 Plan 时使用的表结构（与前端 TableData 对齐）。"""

    model_config = ConfigDict(populate_by_name=True)
    name: str
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    schema_: List[Dict[str, Any]] = Field(default_factory=list, alias="schema")


class ExecutePlanRequest(BaseModel):
    """无状态执行 Plan 的请求：前端携带当前所有表 + Plan。"""

    plan: Plan
    tables: List[ExecuteTable] = Field(min_length=1)


class ExecutePlanResponse(BaseModel):
    """执行 Plan 后返回的新表、Diff 以及新建表列表。"""

    tables: Dict[str, ExecuteTable]
    diff: Dict[str, List[str]]
    newTables: List[str] = Field(default_factory=list)
