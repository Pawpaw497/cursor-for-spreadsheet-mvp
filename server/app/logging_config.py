"""应用级日志配置与请求 trace 上下文。

通过 ContextVar 在中间件与业务代码间传递 trace_id，并在 Formatter 中注入，
便于在控制台用 grep 过滤同一次请求的全链路日志。
"""
from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

# 当前 HTTP 请求的关联 ID（由中间件设置；非请求场景为 None）。
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

_LOG_INITIALIZED = False


class TraceIdFilter(logging.Filter):
    """为每条 LogRecord 注入 trace_id 字段，供 Formatter 使用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        tid = trace_id_var.get()
        record.trace_id = tid if tid else "-"
        return True


def get_trace_id() -> str | None:
    """返回当前上下文中的 trace_id；若未设置则返回 None。

    @return: 请求级 trace id，或 None。
    """
    return trace_id_var.get()


def set_trace_id(trace_id: str | None) -> Any:
    """设置当前上下文的 trace_id；返回 token 供 reset 使用。

    @param trace_id: 关联 ID；None 表示清除。
    @return: ContextVar.set 返回的 token。
    """
    return trace_id_var.set(trace_id)


def reset_trace_id(token: Any) -> None:
    """恢复 trace_id 上下文（请求结束时调用）。

    @param token: set_trace_id 返回的 token。
    """
    trace_id_var.reset(token)


def get_logger(name: str) -> logging.Logger:
    """获取带 spreadsheet 命名空间的 logger。

    @param name: 逻辑模块名，如 api.plan、services.llm。
    @return: 配置好的 Logger 实例。
    """
    return logging.getLogger(f"spreadsheet.{name}")


def init_logging() -> None:
    """初始化根日志：StreamHandler → stdout，统一格式与 LOG_LEVEL。

    幂等：多次调用仅第一次生效。级别由环境变量 LOG_LEVEL 控制，默认 INFO。
    """
    global _LOG_INITIALIZED
    if _LOG_INITIALIZED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler，避免 uvicorn 重载时重复
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] [trace=%(trace_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    trace_filter = TraceIdFilter()
    handler.addFilter(trace_filter)
    handler.setFormatter(fmt)
    root.addHandler(handler)

    # 降低第三方噪声（可按需调整）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _LOG_INITIALIZED = True


def log_exception_traceback() -> bool:
    """是否在为处理异常记录完整 traceback（环境变量 LOG_FULL_TRACEBACK）。"""
    return os.getenv("LOG_FULL_TRACEBACK", "1").lower() in (
        "1",
        "true",
        "yes",
    )
