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


Step = Union[AddColumnStep, TransformColumnStep,
             JoinTablesStep, CreateTableStep]


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
