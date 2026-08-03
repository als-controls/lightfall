"""ClaudeSessionEndpoint — bus delivery policy (auto/queue) for a Claude session.

Pure logic: no widget imports. Wired to a live ``ClaudeAssistantWidget`` by the
widget/panel layer, and driven by unit tests via plain injected callables.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger

_VALID_POLICIES = {"auto", "queue"}


def format_bus_prompt(sender: str, message: str) -> str:
    """Format an incoming bus message as a prompt to inject into the session."""
    return f"[Message from agent '{sender}']\n{message}"


def bus_banner_text(pending: list[tuple[str, str]]) -> str | None:
    """Return the banner text for a list of pending bus messages, or None
    if the banner should be hidden (nothing pending).

    Pure helper shared by the "queue" delivery-policy notification path and
    the post-flush/post-completion residue check, so the visibility decision
    stays testable without constructing any Qt widgets.
    """
    if not pending:
        return None
    if len(pending) == 1:
        sender = pending[0][0]
        return f"1 message from {sender}"
    return f"{len(pending)} pending agent messages"


class ClaudeSessionEndpoint(QObject):
    """Bus endpoint for a Claude session, applying an auto/queue delivery policy.

    - policy "auto": deliver immediately if idle, else queue for later flush.
    - policy "queue": always queue and notify via ``on_queued``.
    """

    message_queued = Signal(str, str)
    message_auto_accepted = Signal(str, str)

    def __init__(
        self,
        name: str,
        description: str,
        *,
        submit: Callable[[str], bool],
        is_busy: Callable[[], bool],
        on_queued: Callable[[str, str], None],
        policy: str = "queue",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.description = description
        self._submit = submit
        self._is_busy = is_busy
        self._on_queued = on_queued
        self._pending: list[tuple[str, str]] = []
        self._policy = "queue"
        self.set_policy(policy)

    @property
    def policy(self) -> str:
        return self._policy

    def set_policy(self, policy: str) -> None:
        if policy not in _VALID_POLICIES:
            raise ValueError(f"policy must be one of {sorted(_VALID_POLICIES)}, got {policy!r}")
        self._policy = policy

    def is_busy(self) -> bool:
        return self._is_busy()

    def pending(self) -> list[tuple[str, str]]:
        return list(self._pending)

    def accept(self, index: int) -> bool:
        """Submit pending[index] via the submit callable.

        On True (submission accepted), remove from pending and return True.
        On False (submission refused), leave in place and return False.
        """
        if index < 0 or index >= len(self._pending):
            return False
        sender, message = self._pending[index]
        if self._submit(format_bus_prompt(sender, message)):
            self._pending.pop(index)
            return True
        return False

    def dismiss(self, index: int) -> tuple[str, str] | None:
        """Remove and return pending[index] without submitting.

        Returns None if out of range.
        """
        if index < 0 or index >= len(self._pending):
            return None
        return self._pending.pop(index)

    def deliver(self, sender: str, message: str) -> str:
        """Deliver (or queue) a message per the current policy."""
        if self._policy == "auto" and not self._is_busy():
            if self._submit(format_bus_prompt(sender, message)):
                self.message_auto_accepted.emit(sender, message)
                return "delivered"
            # submit refused (e.g. a race where the widget became busy between
            # the is_busy() check and the submit call) -- queue instead of
            # silently dropping the message.
            self._pending.append((sender, message))
            self.message_queued.emit(sender, message)
            return "queued"

        self._pending.append((sender, message))
        self.message_queued.emit(sender, message)
        if self._policy == "queue":
            self._on_queued(sender, message)
        return "queued"

    def flush_pending(self) -> int:
        """Submit all pending messages, returning the count submitted.

        If a submit refuses (returns False) partway through, the remaining
        messages -- including the one that was refused -- are re-queued in
        order and flushing stops there.
        """
        pending, self._pending = self._pending, []
        submitted = 0
        for i, (sender, message) in enumerate(pending):
            try:
                ok = self._submit(format_bus_prompt(sender, message))
            except Exception:
                logger.exception("bus endpoint '{}' failed to flush a pending message", self.name)
                ok = False
            if not ok:
                self._pending = pending[i:] + self._pending
                break
            self.message_auto_accepted.emit(sender, message)
            submitted += 1
        return submitted
