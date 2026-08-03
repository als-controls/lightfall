"""ClaudeSessionEndpoint — bus delivery policy (auto/queue) for a Claude session.

Pure logic: no widget imports. Wired to a live ``ClaudeAssistantWidget`` by the
widget/panel layer, and driven by unit tests via plain injected callables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger

_VALID_POLICIES = {"auto", "queue"}


def format_bus_prompt(sender: str, message: str) -> str:
    """Format an incoming bus message as a prompt to inject into the session."""
    return f"[Message from agent '{sender}']\n{message}"


@dataclass
class BusCardEntry:
    """One inline agent-message card's state.

    ``state`` is one of "pending" (awaiting Accept/Dismiss), "auto"
    (auto-accepted, shown for visibility only), or "dismissed" (greyed out,
    kept in the model so the card stays visible but inert).
    """

    sender: str
    message: str
    state: str = "pending"


class BusCardModel:
    """Widget-free bookkeeping for the inline bus-message cards.

    Mirrors (but does not own) ``ClaudeSessionEndpoint``'s ``_pending`` list:
    pending entries are added in the same order the endpoint queues them, so
    ``pending_index_of`` / ``entry_for_pending_index`` track the endpoint's
    ``accept(index)`` / ``dismiss(index)`` indices without storing raw ints
    that would go stale as the endpoint's list mutates.
    """

    def __init__(self) -> None:
        self._entries: list[BusCardEntry] = []

    @property
    def entries(self) -> list[BusCardEntry]:
        return list(self._entries)

    def add_pending(self, sender: str, message: str) -> BusCardEntry:
        entry = BusCardEntry(sender, message, "pending")
        self._entries.append(entry)
        return entry

    def add_auto(self, sender: str, message: str) -> BusCardEntry:
        entry = BusCardEntry(sender, message, "auto")
        self._entries.append(entry)
        return entry

    def mark_dismissed(self, entry: BusCardEntry) -> None:
        entry.state = "dismissed"

    def remove(self, entry: BusCardEntry) -> None:
        """Delete an entry entirely (used after a successful Accept)."""
        try:
            self._entries.remove(entry)
        except ValueError:
            pass

    def take_pending(self, sender: str, message: str) -> BusCardEntry | None:
        """Find and remove the first pending entry matching sender/message.

        FIFO match, mirroring ``ClaudeSessionEndpoint._pending`` order. Used
        when an "auto" policy flush resolves a message that was already
        showing as a pending card (queued while busy, then auto-submitted
        once the query completed) so the stale pending card can be replaced
        by an auto-accepted one instead of leaving two cards for one message.
        """
        for e in self._entries:
            if e.state == "pending" and e.sender == sender and e.message == message:
                self._entries.remove(e)
                return e
        return None

    def pending_index_of(self, entry: BusCardEntry) -> int | None:
        """Return entry's position among currently-pending entries (add
        order), i.e. the index ``ClaudeSessionEndpoint.accept``/``dismiss``
        expects. None if the entry isn't pending or isn't in the model."""
        if entry.state != "pending":
            return None
        index = 0
        for e in self._entries:
            if e is entry:
                return index
            if e.state == "pending":
                index += 1
        return None

    def entry_for_pending_index(self, index: int) -> BusCardEntry | None:
        i = 0
        for e in self._entries:
            if e.state == "pending":
                if i == index:
                    return e
                i += 1
        return None

    def pending_count(self) -> int:
        return sum(1 for e in self._entries if e.state == "pending")

    def rebuild_from_pending(self, pending: list[tuple[str, str]]) -> list[BusCardEntry]:
        """Discard all tracked entries (auto/dismissed transcript history and
        any stale pending ones) and re-seed the model from the endpoint's
        current ``_pending`` list, in order.

        Used on conversation reset: the chat transcript is cleared, but
        messages still queued in the endpoint were never delivered and must
        stay actionable, so they get fresh "pending" entries whose order
        matches the endpoint's list (preserving ``pending_index_of`` /
        ``entry_for_pending_index`` semantics for accept/dismiss).

        Returns the newly created entries, in the same order as ``pending``.
        """
        self._entries = []
        new_entries = []
        for sender, message in pending:
            entry = self.add_pending(sender, message)
            new_entries.append(entry)
        return new_entries


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
