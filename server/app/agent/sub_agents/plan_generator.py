"""Plan 生成阶段符号：与 `decision` 同语义；LangGraph 中由 `orchestrator` 的 llm 节点承担。"""

# 与 decision.py 中的 decision / 校验逻辑解耦，避免重复维护。
# 若 future 在独立 LLM 链上生成 plan，可在此实现。
