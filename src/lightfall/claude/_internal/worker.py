"""Compatibility shim: the Claude worker is now AgentWorker + ClaudeCodeBackend."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject

from lightfall.agents.backends.claude_code.backend import ClaudeCodeBackend, _summarize_sdk_msg
from lightfall.agents.worker import AgentWorker

__all__ = ["PersistentClaudeWorker", "_summarize_sdk_msg"]


class PersistentClaudeWorker(AgentWorker):
    """AgentWorker driving a ClaudeSDKClient (legacy constructor signature)."""

    def __init__(
        self,
        client: Any,
        initial_prompt: str | None = None,
        permission_manager: Any | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(
            ClaudeCodeBackend(client, initial_prompt=initial_prompt),
            permission_manager=permission_manager,
            parent=parent,
        )
        self.client = client
