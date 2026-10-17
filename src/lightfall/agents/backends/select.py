"""Pick the agent backend from the ``agent_backend`` preference.

``"agy"`` selects the Antigravity CLI backend; anything else (default
``"claude-code"``) keeps the Claude Code backend.
"""
from __future__ import annotations

from lightfall.agents.backend import SessionStore

AGY = "agy"
CLAUDE_CODE = "claude-code"


def backend_kind() -> str:
    """The configured backend kind: ``"agy"`` or ``"claude-code"``."""
    from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

    return ClaudeSettingsProvider.get_agent_backend()


def make_session_store(kind: str, cwd: str) -> SessionStore:
    """Session listing/transcripts for ``kind``, scoped to ``cwd``.

    agy reads the main assistant's per-spec store (``spec_home(None)``).
    """
    if kind == AGY:
        from lightfall.agents.backends.agy.home import spec_home
        from lightfall.agents.backends.agy.sessions import AgySessionStore

        return AgySessionStore(spec_home(None), cwd)
    from lightfall.agents.backends.claude_code.sessions import ClaudeSessionStore

    return ClaudeSessionStore(cwd)
