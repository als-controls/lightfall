"""Claude Code session listing / transcript loading (per-cwd CLI store)."""
from __future__ import annotations

from typing import Any

from lightfall.agents.backend import SessionInfo, TranscriptEntry
from lightfall.utils.logging import logger


def _sdk_list_sessions(directory: str, limit: int) -> list[Any]:
    from claude_agent_sdk import list_sessions

    return list_sessions(directory=directory, limit=limit)


def _sdk_get_session_messages(session_id: str, directory: str) -> list[Any]:
    from claude_agent_sdk import get_session_messages

    return get_session_messages(session_id, directory=directory)


class ClaudeSessionStore:
    def __init__(self, directory: str) -> None:
        self._directory = directory

    def list_sessions(self, limit: int = 15) -> list[SessionInfo]:
        try:
            infos = _sdk_list_sessions(self._directory, limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug("list_sessions failed: {}", exc)
            return []
        out: list[SessionInfo] = []
        for info in infos:
            title = (
                getattr(info, "custom_title", None)
                or getattr(info, "first_prompt", None)
                or getattr(info, "summary", None)
                or info.session_id[:8]
            )
            title = (title[:60] + "…") if len(title) > 60 else title
            out.append(SessionInfo(info.session_id, title))
        return out

    def load_transcript(self, session_id: str) -> list[TranscriptEntry]:
        from lightfall.claude.transcript import extract_message_text

        try:
            messages = _sdk_get_session_messages(session_id, self._directory)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load transcript for {}: {}", session_id, exc)
            return []
        return [TranscriptEntry(*extract_message_text(m)) for m in messages]
