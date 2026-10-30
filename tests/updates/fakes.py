"""A stand-in for UpdateChecker that the indicator and dialog tests drive by hand."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from lightfall.updates.git import RepoState
from lightfall.updates.model import CheckResult


def repo(name: str, **kw) -> RepoState:
    base = {"root": Path("/src") / name, "dists": (name,), "branch": "master",
            "default_branch": "master", "remote": "origin", "dirty": False, "head": "aaa1111",
            "ahead": 0, "behind": 0, "remote_head": "aaa1111"}
    base.update(kw)
    return RepoState(**base)


class FakeChecker(QObject):
    result_changed = Signal(object)
    restart_pending_changed = Signal(bool)

    def __init__(self, result: CheckResult = CheckResult.EMPTY, allowed: bool = True):
        super().__init__()
        self.result = result
        self.restart_pending = False
        self.allowed = allowed
        self.checks: list[bool] = []
        self.gate_refreshes = 0
        self.active = allowed
        self.applying = False

    def begin_apply(self) -> bool:
        if self.applying:
            return False
        self.applying = True
        return True

    def end_apply(self) -> None:
        self.applying = False

    def is_allowed(self) -> bool:
        return self.allowed

    def refresh_gate(self) -> None:
        self.gate_refreshes += 1

    def check_now(self, fetch: bool = True) -> None:
        self.checks.append(fetch)

    def mark_restart_pending(self) -> None:
        self.restart_pending = True
        self.restart_pending_changed.emit(True)

    def deactivate(self) -> None:
        self.active = False
        self.publish(CheckResult.EMPTY)

    def publish(self, result: CheckResult) -> None:
        self.result = result
        self.result_changed.emit(result)
