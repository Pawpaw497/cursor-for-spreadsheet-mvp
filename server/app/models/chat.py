"""Chat history related Pydantic models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


ChatRole = Literal["user", "assistant", "system"]


class ChatMessage(BaseModel):
    """Single chat message normalized from agent transcripts."""

    id: str
    session_id: str = Field(alias="sessionId")
    role: ChatRole
    content: str
    created_at: datetime = Field(alias="createdAt")
    project_id: Optional[str] = Field(default=None, alias="projectId")
    meta: Optional[dict] = None


class ChatSession(BaseModel):
    """Aggregated chat session composed of multiple messages."""

    id: str
    title: str
    project_id: Optional[str] = Field(default=None, alias="projectId")
    messages: list[ChatMessage] = Field(default_factory=list)
