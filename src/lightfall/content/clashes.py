"""Name clashes found while loading scoped content, kept for the process."""
from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class Clash:
    type: str
    name: str
    winner: str  # source label that kept the name
    loser: str  # source label that lost it
    loaded_loser: bool  # True when the loser's code had already run before it was unloaded


class ClashLog(QObject):
    """Process-wide record of clashes. Use ClashLog.get_instance(); reset_instance() is for tests."""

    added = Signal(list)  # the clashes that were new in a report() call

    _instance: ClashLog | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._clashes: list[Clash] = []
        self._seen: set[Clash] = set()
        # Reports can arrive from worker threads (skills resolve inside agent tool calls).
        self._data_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> ClashLog:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def report(self, clashes: Iterable[Clash]) -> None:
        new: list[Clash] = []
        with self._data_lock:
            for clash in clashes:
                if clash not in self._seen:
                    self._seen.add(clash)
                    self._clashes.append(clash)
                    new.append(clash)
        if new:  # emit outside the lock; queued to receivers living on another thread
            self.added.emit(new)

    def all(self) -> list[Clash]:
        with self._data_lock:
            return list(self._clashes)
