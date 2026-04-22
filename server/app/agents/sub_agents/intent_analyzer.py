from app.agents.base_agent import BaseAgent
from langgraph.graph import END, StateGraph
from server.app.agents.state import AgentState


class UserIntentAnalyzer(BaseAgent):
    def __init__(self, agent_id: str, name: str, description: str) -> None:
        super().__init__(agent_id, name, description)

    def _build_graph(self):
        graph = StateGraph(AgentState)
        # Add nodes
        graph.add_node("intent_extraction", self._intent_extraction)
        graph.add_node("column_grounding", self._column_grounding)
        graph.add_node("operation_analysis", self._operation_analysis)
        graph.add_node("condition_extraction", self._condition_extraction)
        graph.add_node("format_response", self._format_response)
        # Add edges
        graph.set_entry_point("intent_extraction")
        graph.add_edge("intent_extraction", "column_grounding")
        graph.add_edge("column_grounding", "operation_analysis")
        graph.add_edge("operation_analysis", "condition_extraction")
        graph.add_edge("condition_extraction", END)

        return graph

    async def _intent_extraction(self, state: AgentState):
        ...

    async def _column_grounding(self, state: AgentState):
        ...

    async def _operation_analysis(self, state: AgentState):
        ...

    async def _condition_extraction(self, state: AgentState):
        ...

    async def _format_response(self, state: AgentState):
        ...