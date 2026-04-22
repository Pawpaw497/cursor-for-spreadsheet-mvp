"""LLM 提示词构建与 JSON 解析。全部使用 Pydantic 定义消息与用户内容结构。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

from app.services.prompt_content import (
    PROJECT_SYSTEM,
    SPREADSHEET_SYSTEM,
    build_column_stats_text,
)

Role = Literal["system", "user", "assistant"]


# --- Pydantic 消息与用户内容模型 ---


class Message(BaseModel):
    """单条聊天消息，对应 API 的 messages 数组元素。"""

    role: Role
    content: str

    def to_dict(self) -> dict:
        """供 llm 模块 _messages_to_payload 使用，与原有调用兼容。"""
        return self.model_dump()

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> Message:
        return cls(role="assistant", content=content)


class SingleTableUserContent(BaseModel):
    """单表场景下发给 LLM 的用户内容结构（schema + 样本行 + 用户请求）。"""

    user_prompt: str = Field(alias="user_prompt")
    schema_: List[Dict[str, Any]] = Field(alias="schema")
    sample_rows: List[Dict[str, Any]] = Field(
        default_factory=list, alias="sample_rows")

    model_config = {"populate_by_name": True}

    def to_prompt_string(self) -> str:
        """序列化为 prompt 中的 user 消息正文。"""
        schema_str = json.dumps(self.schema_, ensure_ascii=False, indent=2)
        rows_str = json.dumps(self.sample_rows, ensure_ascii=False, indent=2)
        col_keys: List[str] = []
        for c in self.schema_:
            if isinstance(c, dict) and c.get("key"):
                col_keys.append(str(c["key"]))
        stats = build_column_stats_text(
            self.sample_rows, col_keys or None
        )
        return (
            "Spreadsheet schema:\n"
            f"{schema_str}\n\n"
            "Sample rows:\n"
            f"{rows_str}\n\n"
            f"{stats}"
            "User request:\n"
            f"{self.user_prompt}\n"
        )


class TableBlock(BaseModel):
    """多表场景下的一张表：表名 + schema + 样本行。"""

    name: str
    schema_: List[Dict[str, Any]] = Field(default_factory=list, alias="schema")
    sample_rows: List[Dict[str, Any]] = Field(
        default_factory=list, alias="sample_rows")

    model_config = {"populate_by_name": True}


class ProjectUserContent(BaseModel):
    """多表场景下发给 LLM 的用户内容结构。"""

    tables: List[TableBlock] = Field(default_factory=list)
    user_prompt: str = ""

    def to_prompt_string(self) -> str:
        """序列化为 prompt 中的 user 消息正文。"""
        parts = ["Project has multiple tables:\n"]
        for t in self.tables:
            schema_str = json.dumps(t.schema_, ensure_ascii=False, indent=2)
            rows_str = json.dumps(t.sample_rows, ensure_ascii=False, indent=2)
            col_keys: List[str] = []
            for c in t.schema_:
                if isinstance(c, dict) and c.get("key"):
                    col_keys.append(str(c["key"]))
            stats = build_column_stats_text(
                t.sample_rows, col_keys or None
            )
            parts.append(f"Table '{t.name}':")
            parts.append(f"  schema: {schema_str}")
            parts.append(f"  sample rows: {rows_str}\n")
            if stats:
                for line in stats.rstrip("\n").split("\n"):
                    parts.append(f"  {line}\n")
        parts.append(f"User request:\n{self.user_prompt}\n")
        return "".join(parts)


# --- 单表 / 多表 Prompt 封装（系统提示词见 prompt_content.py）---


def _normalize_schema(schema: Any) -> List[Dict[str, Any]]:
    """将 schema（List[ColumnSchema] 或 List[dict]）统一为 List[dict]。"""
    if not schema:
        return []
    result: List[Dict[str, Any]] = []
    for c in schema:
        if hasattr(c, "model_dump"):
            result.append(c.model_dump())
        elif isinstance(c, dict):
            result.append(c)
        else:
            result.append(dict(c))
    return result


class SpreadsheetPrompt:
    """单表计划用的 prompt：系统提示 + 用户内容（schema + 样本行 + 用户请求）。"""

    def __init__(self, system: str | None = None) -> None:
        self.system = system if system is not None else SPREADSHEET_SYSTEM

    def build_user_content(self, user_prompt: str, schema: Any, sample_rows: Any) -> str:
        """构建单表 user 消息正文。schema 可为 List[ColumnSchema] 或 List[dict]。"""
        content = SingleTableUserContent(
            user_prompt=user_prompt,
            schema=_normalize_schema(schema),
            sample_rows=list(sample_rows) if sample_rows else [],
        )
        return content.to_prompt_string()

    def single_turn_messages(self, user_content: str) -> List[Message]:
        return [Message.system(self.system), Message.user(user_content)]

    def messages(self, user_prompt: str, schema: Any, sample_rows: Any) -> List[Message]:
        """单轮：system + 根据 schema/sample_rows 构建的 user 消息。"""
        return self.single_turn_messages(
            self.build_user_content(user_prompt, schema, sample_rows)
        )


class ProjectPrompt:
    """多表/项目计划用的 prompt：系统提示 + 用户内容（多表 schema/样本 + 用户请求）。"""

    def __init__(self, system: str | None = None) -> None:
        self.system = system if system is not None else PROJECT_SYSTEM

    def build_user_content(self, user_prompt: str, tables: List[dict]) -> str:
        """构建多表 user 消息正文。tables 每项含 name, schema/schema_, sampleRows/sample_rows。"""
        blocks = []
        for t in tables:
            schema_val = _normalize_schema(t.get("schema") or t.get("schema_"))
            sample_rows = t.get("sampleRows") or t.get("sample_rows") or []
            blocks.append(
                TableBlock(
                    name=t["name"],
                    schema=schema_val,
                    sample_rows=list(sample_rows),
                )
            )
        content = ProjectUserContent(tables=blocks, user_prompt=user_prompt)
        return content.to_prompt_string()

    def single_turn_messages(self, user_content: str) -> List[Message]:
        return [Message.system(self.system), Message.user(user_content)]

    def messages(self, user_prompt: str, tables: List[dict]) -> List[Message]:
        """单轮：system + 根据 tables 构建的 user 消息。"""
        return self.single_turn_messages(self.build_user_content(
            user_prompt, tables))


# 兼容旧用法：模块级常量与函数
SYSTEM_PROMPT = SPREADSHEET_SYSTEM
SYSTEM_PROMPT_PROJECT = PROJECT_SYSTEM

_default_spreadsheet_prompt = SpreadsheetPrompt()
_default_project_prompt = ProjectPrompt()


def messages_from_chat(system: str, user: str) -> List[Message]:
    """从 system 与 user 文本构建 API 所需的 messages 列表（单轮）。"""
    return [Message.system(system), Message.user(user)]


def build_messages(
    turns: List[Message],
    *,
    system: str | None = None,
) -> List[Message]:
    """构建多轮消息列表。若有 system 则置于首条；turns 为按顺序的 user/assistant 轮次。"""
    out: List[Message] = []
    if system is not None:
        out.append(Message.system(system))
    out.extend(turns)
    return out


def build_user_prompt(user_prompt: str, schema: Any, sample_rows: Any) -> str:
    return _default_spreadsheet_prompt.build_user_content(
        user_prompt, schema, sample_rows)


def build_project_user_prompt(user_prompt: str, tables: List[dict]) -> str:
    return _default_project_prompt.build_user_content(user_prompt, tables)


def extract_json(text: str) -> str:
    cleaned = re.sub(r"```json|```", "", text).strip()
    return cleaned
