"""AgentState：Agent 循环中每轮维护的状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from app.models.plan import (AgentProjectPlanRequest, PlanRequest,
                             ProjectPlanRequest, TableInfo)
from pydantic import BaseModel


@dataclass
class TableContext:
    """单表上下文：供 Agent 读取的表格信息（schema + 样本行）。"""
    name: str
    schema: List[Dict[str, Any]]
    sample_rows: List[Dict[str, Any]]

    @classmethod
    def from_table_info(cls, t: TableInfo) -> "TableContext":
        return cls(name=t.name, schema=t.schema_, sample_rows=t.sampleRows)


class AgentState(BaseModel):
    """
    Agent 的显式状态：循环每轮更新，供 decision 读取、流式推送与记忆使用。

    字段说明：
    - tables: 当前项目下的表上下文（名、schema、样本行），工具与 prompt 基于此。
    - messages: 与 LLM 的对话历史（system/user/assistant + 工具结果），每轮追加。
    - applied_plans_summary: 本会话已应用计划的简短摘要，供多轮指代（如「把刚才那列删掉」）。
    - current_turn: 当前轮次（0-based），用于限轮与日志。
    - max_turns: 最大轮数，超过则强制结束。
    - user_prompt: 用户本轮/首次输入的自然语言请求。
    - model_source / cloud_model_id / local_model_id: LLM 调用配置。
    """

    tables: List[TableContext]
    messages: List[Dict[str, str]]
    applied_plans_summary: Optional[str] = None
    conversation: List[Dict[str, str]] = []
    current_turn: int = 0
    max_turns: int = 10
    user_prompt: str = ""
    model_source: Literal["cloud", "local"] = "cloud"
    cloud_model_id: Optional[str] = None
    local_model_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """便于日志或 SSE 推送的字典表示（不含完整 messages 时可截断）。

        @return: 返回包含部分关键 Agent 状态统计的 dict，用于 SSE 事件或日志简报。
        """
        return {
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "tables_count": len(self.tables),
            "messages_count": len(self.messages),
            "applied_plans_summary": self.applied_plans_summary,
            "conversation_turns": len(self.conversation),
        }


def initial_state_from_plan_request(req: PlanRequest) -> AgentState:
    """从单表计划请求构建初始 AgentState。"""
    tables = [
        TableContext(
            name="Sheet1",
            schema=req.schema_,
            sample_rows=req.sampleRows,
        )
    ]
    # 首条 user 内容由 decision / prompts 层组装（schema + sample + user_prompt）
    messages: List[Dict[str, str]] = []
    return AgentState(
        tables=tables,
        messages=messages,
        user_prompt=req.prompt,
        model_source=req.modelSource or "cloud",
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )


def initial_state_from_project_request(req: ProjectPlanRequest) -> AgentState:
    """从多表/项目计划请求构建初始 AgentState。"""
    tables = [TableContext.from_table_info(t) for t in req.tables]
    messages: List[Dict[str, str]] = []
    return AgentState(
        tables=tables,
        messages=messages,
        user_prompt=req.prompt,
        model_source=req.modelSource or "cloud",
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )


def initial_state_from_agent_project_request(
    req: AgentProjectPlanRequest,
) -> AgentState:
    """从带历史的 Agent 请求构建初始 AgentState。"""
    tables = [TableContext.from_table_info(t) for t in req.tables]
    # 历史对话：直接拼进 messages，后续 decision 会继续在其基础上对话
    history_msgs: List[Dict[str, str]] = [
        {"role": turn.role, "content": turn.content} for turn in (req.history or [])
    ]
    return AgentState(
        tables=tables,
        messages=history_msgs,
        applied_plans_summary=req.appliedPlansSummary,
        conversation=history_msgs,
        user_prompt=req.prompt,
        model_source=req.modelSource or "cloud",
        cloud_model_id=req.cloudModelId,
        local_model_id=req.localModelId,
    )
