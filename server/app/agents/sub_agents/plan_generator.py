from langgraph.graph import END, StateGraph

from server.app.agents.base_agent import BaseAgent
from server.app.agents.state import AgentState


class PlanGenerator(BaseAgent):
    def __init__(self, agent_id: str, name: str, description: str) -> None:
        super().__init__(agent_id, name, description)

    def _build_graph(self):
        graph = StateGraph(AgentState)
        # add nodes
        graph.add_node("plan_draft", self._plan_draft)
        graph.add_node("normalize_plan", self._normalize_plan)
        graph.add_node("static_validation", self._static_validation)
        graph.add_node("dry_run", self._dry_run)
        graph.add_node("scoring", self._scoring)
        graph.add_node("finalizer", self._finalizer)
        # set edges
        graph.set_entry_point("plan_draft")
        graph.add_edge("plan_draft", "normalize_plan")
        graph.add_edge("normalize_plan", "static_validation")
        graph.add_edge("static_validation", "dry_run")
        graph.add_edge("dry_run", "scoring")
        graph.add_edge("scoring", "finalizer")
        graph.add_edge("finalizer", END)

        return graph

    async def _plan_draft(self, state: AgentState):
        ...

    async def _normalize_plan(self, state: AgentState):
        ...

    async def _static_validation(self, state: AgentState):
        ...

    async def _dry_run(self, state: AgentState):
        ...

    async def _scoring(self, state: AgentState):
        ...

    async def _finalizer(self, state: AgentState):
        ...
