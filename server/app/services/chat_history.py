"""Chat history service: read agent transcripts and normalize to ChatMessage.

This module scans agent transcript JSONL files produced by Cursor, parses them
into backend ChatMessage models, and provides simple filtering helpers for API
layers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from app.config import settings
from app.models import ChatMessage

logger = logging.getLogger(__name__)


@dataclass
class _RawTranscriptMessage:
    """Internal representation of a single raw transcript line."""

    session_id: str
    index: int
    role: str
    payload: dict
    created_at: datetime


def _project_root() -> Path:
    """Best-effort project root detection (aligned with load.py helper).

    Returns:
        Path pointing to repository root directory.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if parent.name == "server":
            # <project>/server → return <project>
            return parent.parent
    # Fallback for unexpected layouts:
    # assume <project>/server/app/services/chat_history.py so parents[3] is <project>.
    return current.parents[3]


def _resolve_transcripts_dir() -> Optional[Path]:
    """Resolve the base directory containing agent transcript JSONL files.

    Preference order:
      1. Explicit path from settings.AGENT_TRANSCRIPTS_DIR.
      2. First directory matching <project>/.cursor/projects/*/agent-transcripts.

    Returns:
        Directory path if found; otherwise None.
    """
    if settings.AGENT_TRANSCRIPTS_DIR:
        p = Path(settings.AGENT_TRANSCRIPTS_DIR).expanduser()
        if p.is_dir():
            return p
        logger.warning(
            "AGENT_TRANSCRIPTS_DIR=%s is not a directory or does not exist",
            p,
        )

    root = _project_root()
    cursor_root = root / ".cursor" / "projects"
    if not cursor_root.is_dir():
        return None

    candidates = list(cursor_root.glob("*/agent-transcripts"))
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _iter_session_files(base_dir: Path) -> Iterable[Path]:
    """Yield main session JSONL files under the transcripts directory.

    This skips sub-agent transcript files under any ``subagents`` folder and
    only returns top-level session JSONL paths.
    """
    for session_dir in base_dir.iterdir():
        if not session_dir.is_dir():
            continue
        if session_dir.name == "subagents":
            continue
        main_file = session_dir / f"{session_dir.name}.jsonl"
        if main_file.is_file():
            yield main_file


def _extract_text_from_payload(payload: dict) -> str:
    """Extract human-readable text from Cursor transcript payload.

    Args:
        payload: Raw JSON object for a single line in transcript.

    Returns:
        Concatenated text content suitable for chat UI.
    """
    message = payload.get("message") or {}
    content = message.get("content")
    if not content:
        # Some records may store plain string content directly.
        raw_content = message.get("content")
        text = raw_content if isinstance(raw_content, str) else ""
        return str(text or "")

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    if isinstance(content, str):
        return content

    return ""


def _extract_project_id(payload: dict) -> Optional[str]:
    """Try to infer projectId from payload, if any.

    This is intentionally conservative: if the field does not exist, None is
    returned and higher layers treat it as a global or project-agnostic
    message.
    """
    for key in ("projectId", "project_id"):
        if key in payload:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    message = payload.get("message") or {}
    for key in ("projectId", "project_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _read_session_file(path: Path) -> List[_RawTranscriptMessage]:
    """Parse a single session JSONL file into internal messages.

    Args:
        path: Path to JSONL file.

    Returns:
        List of raw transcript messages in file order.
    """
    session_id = path.parent.name
    try:
        base_time = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        base_time = datetime.now(tz=timezone.utc)

    messages: List[_RawTranscriptMessage] = []
    try:
        with path.open(encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(
                        "Skip invalid JSONL line in %s: %s",
                        path,
                        line,
                    )
                    continue
                role = payload.get("role") or "system"
                created_at = base_time + timedelta(milliseconds=idx)
                messages.append(
                    _RawTranscriptMessage(
                        session_id=session_id,
                        index=idx,
                        role=str(role),
                        payload=payload,
                        created_at=created_at,
                    )
                )
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Failed to read transcript file %s: %s", path, exc)
        return []
    return messages


def _normalize_raw_messages(
    raw_messages: Iterable[_RawTranscriptMessage],
) -> List[ChatMessage]:
    """Convert internal raw messages into public ChatMessage models."""
    out: List[ChatMessage] = []
    for raw in raw_messages:
        text = _extract_text_from_payload(raw.payload)
        project_id = _extract_project_id(raw.payload)
        role = raw.role if raw.role in ("user", "assistant", "system") else "system"
        msg = ChatMessage(
            id=f"{raw.session_id}:{raw.index}",
            sessionId=raw.session_id,
            role=role,
            content=text,
            createdAt=raw.created_at,
            projectId=project_id,
            meta={"raw": raw.payload},
        )
        out.append(msg)
    return out


def load_chat_history(
    *,
    project_id: Optional[str] = None,
    limit: int = 200,
) -> List[ChatMessage]:
    """Load chat history from agent transcripts as normalized ChatMessage list.

    Args:
        project_id: Optional project identifier to filter messages. When None,
            messages from all sessions are returned.
        limit: Maximum number of messages to return, ordered by time
            descending. Values are clamped to [1, 1000].

    Returns:
        List of ChatMessage ordered by created_at descending.
    """
    limit = max(1, min(limit, 1000))
    base_dir = _resolve_transcripts_dir()
    if not base_dir:
        logger.info("No agent transcripts directory found; returning empty history.")
        return []

    all_raw: List[_RawTranscriptMessage] = []
    for path in _iter_session_files(base_dir):
        all_raw.extend(_read_session_file(path))

    if not all_raw:
        return []

    messages = _normalize_raw_messages(all_raw)

    if project_id:
        messages = [m for m in messages if m.project_id == project_id]

    messages.sort(key=lambda m: m.created_at, reverse=True)
    if len(messages) > limit:
        return messages[:limit]
    return messages

