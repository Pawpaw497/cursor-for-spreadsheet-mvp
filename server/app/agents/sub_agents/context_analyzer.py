from app.agents.base_agent import BaseAgent
# from langgraph.graph import StateGraph

from server.app.agents.state import AgentState


class TableContextAnalyzerAgent(BaseAgent):
    def __init__(self, agent_id: str, name: str, description: str) -> None:
        super().__init__(agent_id, name, description)

    def _build_graph(self):
        ...

    async def _schema_extraction(self, state: AgentState):
        """
        extract basic schema of the table
        """
        ...

    async def _statistical_profiling(self, state: AgentState):
        """
        Profiling each column
        """
        ...

    async def _column_semantic_inference(self, state: AgentState):
        ...

    async def _column_embedding(self, state: AgentState):
        ...

    async def _table_topic_inference(self, state: AgentState):
        ...

    async def _granularity_inference(self, state: AgentState):
        ...

    async def llm_semantic_summary(self, state: AgentState):
        """
        使用 LLM 对表格进行语义摘要。

        @param state: 当前 Agent 状态，包含表格上下文等信息。
        @return: None。未来可拓展为返回摘要信息或修改 AgentState。
        """
        ...
