from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

InferredColType = Literal["numeric", "boolean", "date", "string", "mixed", "empty"]


class ColumnProfile(BaseModel):
    name: str
    inferred_type: InferredColType
    count: int
    null_count: int
    null_ratio: float
    distinct_count: int
    off_type_count: int = 0
    min_val: Optional[str] = None
    max_val: Optional[str] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    top_values: list[tuple[str, int]] = Field(default_factory=list)


class TableProfile(BaseModel):
    table_name: str
    total_row_count: int
    col_count: int
    columns: list[ColumnProfile] = Field(default_factory=list)
    profile_sampled: bool = False
    topic: Optional[str] = None
    description: Optional[str] = None
    granularity: Optional[str] = None


class DataContext(BaseModel):
    tables: list[TableProfile] = Field(default_factory=list)


class TableIntent(BaseModel):
    """intent_analyzer 单表分类结果：回填 TableProfile.topic/description/granularity。"""

    table_name: str
    topic: Optional[str] = None
    description: Optional[str] = None
    granularity: Optional[str] = None


class TableIntentBatch(BaseModel):
    """intent_analyzer 批量分类的结构化输出（pydantic-ai result_type）。"""

    tables: list[TableIntent] = Field(default_factory=list)


PrePlanFallbackReason = Literal["timeout", "low_confidence", "error"]


class PrePlanContext(BaseModel):
    """pre_plan 子代理对 DataContext 做表级裁剪后的输出契约。

    见 .cursor/plans/agent-evolution-p1-v2-core.plan.md（p1-pre-plan-contract）。

    - ``confidence`` < 0.5 语义上等价于 ``fallback_reason="low_confidence"``：
      调用方（``pre_plan`` 图节点）须回退到全量 ``DataContext`` + 全表 schema，
      而不是原样透传低置信度的裁剪结果。该 fallback 行为由调用方实现，本模型
      只承载契约，不做自动改写。
    - ``selected_table_names`` 允许为空——activeTable/selection 必保留的守护
      规则由 p1-pre-plan-guards 强制，不在此模型内校验。
    """

    selected_table_names: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    fallback_reason: Optional[PrePlanFallbackReason] = None


class PrePlanSelection(BaseModel):
    """pre_plan LLM 调用的结构化输出（pydantic-ai result_type）。

    只承载 LLM 决定的字段——不含 ``fallback_reason``，该字段由调用方
    （``app.agent.sub_agents.pre_plan``）根据 confidence 阈值/超时/异常赋值。
    """

    selected_table_names: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
