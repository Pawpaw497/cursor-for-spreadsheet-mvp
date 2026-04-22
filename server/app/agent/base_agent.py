"""可扩展的 Agent 基类占位（工业级多子图 agent 时在此对接 LangGraph）。

当前主线编排由 `orchestrator.py` 的 StateGraph 完成，本类不再在 MVP 中实例化或调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.agent.state import AgentState


class BaseAgent(ABC):
    """抽象子代理骨架：后续若拆独立 LLM+图，可在此实现 `_build_graph` 与 `invoke`。"""

    def __init__(self, agent_id: str, name: str, description: str) -> None:
        self.agent_id = agent_id
        self.name = name
        self.description = description

    @abstractmethod
    def _build_graph(self) -> Any:
        """子类可返回已编译的 LangGraph 或其他执行图。"""

    @abstractmethod
    async def _process_request(self, state: AgentState) -> AgentState:
        """子类对单次请求的主逻辑。"""
