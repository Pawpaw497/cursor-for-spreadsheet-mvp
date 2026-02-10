"""LLM 提示词构建与 JSON 解析。"""
import json
import re
from typing import Any, List, Literal

Role = Literal["system", "user", "assistant"]


class Message:
    """单条聊天消息，对应 API 的 messages 数组元素。"""

    __slots__ = ("role", "content")

    def __init__(self, role: Role, content: str) -> None:
        self.role = role
        self.content = content

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls("system", content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls("user", content)

    @classmethod
    def assistant(cls, content: str) -> "Message":
        return cls("assistant", content)


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


# --- 单表 / 多表 Prompt 封装 ---

_SPREADSHEET_SYSTEM = """You are an agent that edits a spreadsheet by generating an execution plan.

Output rules (VERY IMPORTANT):
- Output ONLY valid JSON.
- Do NOT include explanations, markdown, or code fences.
- Do NOT include any text outside the JSON.
- The JSON must strictly follow the schema below.
- If ambiguous, choose the simplest reasonable interpretation.

Schema:
{
  "intent": string,
  "steps": [
    { "action": "add_column", "name": string, "expression": string, "table"?: string, "note"?: string }
    |
    { "action": "transform_column", "column": string, "transform": "trim"|"lower"|"upper"|"replace"|"parse_date", "args"?: object, "table"?: string, "note"?: string }
  ]
}

Rules:
- add_column.expression is a JavaScript expression evaluated as (row) => expression
- Use row.<columnName> to access values
- transform_column.replace args: {"from": string, "to": string}
- transform_column.parse_date args: {"formatHint"?: string}
"""

_PROJECT_SYSTEM = """You are an agent that edits a multi-table spreadsheet project by generating an execution plan.

Output rules (VERY IMPORTANT):
- Output ONLY valid JSON.
- Do NOT include explanations, markdown, or code fences.
- Do NOT include any text outside the JSON.
- The JSON must strictly follow the schema below.
- If ambiguous, choose the simplest reasonable interpretation.

Schema:
{
  "intent": string,
  "steps": [
    { "action": "add_column", "name": string, "expression": string, "table"?: string, "note"?: string }
    |
    { "action": "transform_column", "column": string, "transform": "trim"|"lower"|"upper"|"replace"|"parse_date", "args"?: object, "table"?: string, "note"?: string }
    |
    { "action": "join_tables", "left": string, "right": string, "leftKey": string, "rightKey": string, "resultTable": string, "joinType"?: "inner"|"left"|"right", "note"?: string }
    |
    { "action": "create_table", "name": string, "source": string, "expression"?: string, "note"?: string }
  ]
}

Rules:
- "table" in add_column/transform_column: target table name; omit if only one table.
- add_column.expression: JavaScript (row) => expression; use row.<col> for values.
- transform_column.replace args: {"from": string, "to": string}; parse_date args: {"formatHint"?: string}
- join_tables: join left and right tables on leftKey/rightKey; resultTable is the new table name.
- create_table: create new table from source; expression is (rows)=>filtered rows, or omit for full copy.
"""


class SpreadsheetPrompt:
    """单表计划用的 prompt：系统提示 + 用户内容（schema + 样本行 + 用户请求）。"""

    def __init__(self, system: str | None = None) -> None:
        self.system = system if system is not None else _SPREADSHEET_SYSTEM

    def build_user_content(self, user_prompt: str, schema: Any, sample_rows: Any) -> str:
        return (
            "Spreadsheet schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            "Sample rows:\n"
            f"{json.dumps(sample_rows, ensure_ascii=False, indent=2)}\n\n"
            "User request:\n"
            f"{user_prompt}\n"
        )

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
        self.system = system if system is not None else _PROJECT_SYSTEM

    def build_user_content(self, user_prompt: str, tables: List[dict]) -> str:
        parts = ["Project has multiple tables:\n"]
        for t in tables:
            # 兼容 Pydantic 导出的字段名（schema_ / schema）
            schema_val = t.get("schema") or t.get("schema_")
            sample_rows = t.get("sampleRows") or t.get("sample_rows")
            parts.append(f"Table '{t['name']}':")
            parts.append(
                f"  schema: {json.dumps(schema_val, ensure_ascii=False, indent=2)}"
            )
            parts.append(
                f"  sample rows: {json.dumps(sample_rows, ensure_ascii=False, indent=2)}\n"
            )
        parts.append(f"User request:\n{user_prompt}\n")
        return "".join(parts)

    def single_turn_messages(self, user_content: str) -> List[Message]:
        return [Message.system(self.system), Message.user(user_content)]

    def messages(self, user_prompt: str, tables: List[dict]) -> List[Message]:
        """单轮：system + 根据 tables 构建的 user 消息。"""
        return self.single_turn_messages(self.build_user_content(user_prompt, tables))


# 兼容旧用法：模块级常量与函数
SYSTEM_PROMPT = _SPREADSHEET_SYSTEM
SYSTEM_PROMPT_PROJECT = _PROJECT_SYSTEM

_default_spreadsheet_prompt = SpreadsheetPrompt()
_default_project_prompt = ProjectPrompt()


def build_user_prompt(user_prompt: str, schema: Any, sample_rows: Any) -> str:
    return _default_spreadsheet_prompt.build_user_content(user_prompt, schema, sample_rows)


def build_project_user_prompt(user_prompt: str, tables: List[dict]) -> str:
    return _default_project_prompt.build_user_content(user_prompt, tables)


def extract_json(text: str) -> str:
    cleaned = re.sub(r"```json|```", "", text).strip()
    return cleaned
