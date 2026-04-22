from abc import ABC, abstractmethod
from typing import Any, Dict

from app.agents.state import AgentState
from langgraph.graph import StateGraph


class BaseAgent(ABC):
    def __init__(self, agent_id: str, name: str, description: str):
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.llm = OpenAI(...)  # LangChain integration
        self.graph = self._build_graph()  # LangGraph integration
        self.compiled_graph = self.graph.compile()

    @abstractmethod
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state graph for this agent."""
        pass

    @abstractmethod
    async def _process_request(self, state: AgentState) -> AgentState:
        """Process a request in the agent's main logic."""
        pass

    async def invoke(self, request, context=None) -> Dict[str, Any]:
        """Invoke the agent with a request."""
        # Execute the graph
        result = await self.compiled_graph.ainvoke(initial_state)
        return self._format_response(result)

    async def _format_response(self, result):
        ...
