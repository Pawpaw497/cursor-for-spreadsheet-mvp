"""简单的内存版 ProjectStore，用于在后端保存项目内的表状态。

当前实现为进程内字典 + TTL 过期检查，适合作为 Demo / MVP。
后续可以替换为 Redis / 数据库存储而不影响上层调用方。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class ProjectState:
    """单个项目的表状态。"""

    id: str
    tables: Dict[str, Dict[str, Any]]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ttl_seconds: int = 60 * 60  # 默认 1 小时


class ProjectStore:
    """线程安全的内存项目存储。"""

    def __init__(self) -> None:
        self._projects: Dict[str, ProjectState] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.time()

    def _purge_expired(self) -> None:
        """惰性清理已过期项目。"""
        now = self._now()
        expired: List[str] = []
        for pid, state in self._projects.items():
            if now - state.updated_at > state.ttl_seconds:
                expired.append(pid)
        for pid in expired:
            self._projects.pop(pid, None)

    def create_project(
        self,
        tables: Mapping[str, Dict[str, Any]],
        *,
        ttl_seconds: int | None = None,
    ) -> ProjectState:
        """创建新项目并返回其状态。"""
        with self._lock:
            self._purge_expired()
            pid = uuid.uuid4().hex
            state = ProjectState(
                id=pid,
                tables={k: dict(v) for k, v in tables.items()},
                ttl_seconds=ttl_seconds or 60 * 60,
            )
            self._projects[pid] = state
            return state

    def get_project(self, project_id: str) -> Optional[ProjectState]:
        """获取项目状态，不存在或已过期则返回 None。"""
        with self._lock:
            self._purge_expired()
            state = self._projects.get(project_id)
            if not state:
                return None
            # 访问即视为活跃，刷新更新时间
            state.updated_at = self._now()
            return state

    def update_tables(
        self,
        project_id: str,
        tables: Mapping[str, Dict[str, Any]],
    ) -> Optional[ProjectState]:
        """更新项目中的表集合，返回最新状态，不存在则返回 None。"""
        with self._lock:
            self._purge_expired()
            state = self._projects.get(project_id)
            if not state:
                return None
            state.tables = {k: dict(v) for k, v in tables.items()}
            state.updated_at = self._now()
            return state


# 全局单例实例，供路由与服务直接使用。
project_store = ProjectStore()

