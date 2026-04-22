"""Intent 子代理：MVP 为透传，后续可在此归一化意图标签或做槽位。"""

from app.agent.state import AgentState


def analyze_intent(state: AgentState) -> AgentState:
    """在 plan_generator 之前执行；当前不改变状态。

    @param state: 当前 Agent 状态。
    @return: 同一份或增强后的 state。
    """
    return state
