"""Context 子代理：MVP 为透传，后续可在此做 schema/统计摘要等增强。"""

from app.agent.state import AgentState


def analyze_context(state: AgentState) -> AgentState:
    """在 plan_generator 之前执行；当前不改变状态，仅保留扩展点。

    @param state: 当前 Agent 状态（含多表与 messages）。
    @return: 同一份或增强后的 state。
    """
    return state
