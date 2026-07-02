"""Pure helpers for turning a restored SDK SessionMessage into renderable text.

A ``SessionMessage`` (claude_agent_sdk) has ``.type`` ('user'|'assistant') and
``.message`` — the raw Anthropic API message dict. v1 rehydration shows user
prompts + assistant text + lightweight tool-call chips; tool-result payloads
(noisy machine output) are skipped.
"""
from __future__ import annotations

from typing import Any


def extract_message_text(session_message: Any) -> tuple[str, str, list[str]]:
    """Return ``(role, text, tool_names)`` for a SessionMessage.

    ``role`` is 'user' or 'assistant'. ``text`` joins all text blocks.
    ``tool_names`` lists the names of any ``tool_use`` blocks (for chips).
    """
    role = getattr(session_message, "type", "") or ""
    raw = getattr(session_message, "message", None) or {}
    content = raw.get("content") if isinstance(raw, dict) else None

    text_parts: list[str] = []
    tools: list[str] = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", "") or "")
            elif btype == "tool_use":
                tools.append(block.get("name", "tool") or "tool")
            # tool_result and others: skip in v1
    return role, "\n".join(p for p in text_parts if p), tools
