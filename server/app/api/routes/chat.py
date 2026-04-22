"""Chat history routes."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.logging_config import get_logger
from app.models import ChatMessage
from app.services.chat_history import load_chat_history

router = APIRouter(prefix="/api", tags=["chat"])
log = get_logger("api.chat")


class ChatHistoryResponse(BaseModel):
    """Response schema for /api/chat-history."""

    messages: List[ChatMessage] = Field(default_factory=list)


@router.get("/chat-history", response_model=ChatHistoryResponse)
async def chat_history(
    project_id: Optional[str] = Query(default=None, alias="projectId"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> ChatHistoryResponse:
    """Return normalized chat history messages from agent transcripts.

    Messages are ordered by createdAt descending on the server side.
    """
    log.info(
        "chat_history request project_id=%s limit=%d",
        project_id or "-",
        limit,
    )
    messages = load_chat_history(project_id=project_id, limit=limit)
    log.info("chat_history response count=%d", len(messages))
    return ChatHistoryResponse(messages=messages)

