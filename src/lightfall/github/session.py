"""Session-bound, in-memory GitHub credential.

The token lives only in this process's memory, is never persisted or logged,
and is dropped when the Lightfall user logs out or changes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class GitHubCredential:
    """A GitHub token bound to the Lightfall user who obtained it."""

    token: str = field(repr=False)
    kind: Literal["app", "pat"]
    login: str
    expires_at: datetime | None
    lightfall_user: str

    @property
    def masked(self) -> str:
        """Human-readable status that never includes the token."""
        text = f"signed in as {self.login}"
        if self.expires_at is not None:
            text += f" until {self.expires_at.astimezone():%H:%M}"
        return text


class GitHubSession(QObject):
    """Holds the current GitHub credential, tied to the Lightfall auth session."""

    changed = Signal()

    def __init__(
        self,
        auth_session: Any | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._cred: GitHubCredential | None = None
        self._now = now
        self._auth = auth_session
        if auth_session is not None:
            auth_session.state_changed.connect(self._on_state_changed)
            auth_session.user_changed.connect(self._on_user_changed)

    def set_credential(self, cred: GitHubCredential) -> None:
        with self._lock:
            self._cred = cred
        self.changed.emit()

    def credential(self) -> GitHubCredential | None:
        """Return the live credential, or None if absent, expired or another user's."""
        with self._lock:
            cred = self._cred
        if cred is None:
            return None
        if cred.expires_at is not None and cred.expires_at <= self._now():
            return None
        if self._auth is not None and cred.lightfall_user != self._auth.current_user.username:
            return None
        return cred

    def _held_token(self) -> str:
        with self._lock:
            return self._cred.token if self._cred is not None else ""

    def sign_out(self) -> None:
        with self._lock:
            had = self._cred is not None
            self._cred = None
        if had:
            self.changed.emit()

    def _on_state_changed(self, new_state: Any, _old_state: Any) -> None:
        from lightfall.auth.session import AuthState

        if new_state != AuthState.AUTHENTICATED:
            self.sign_out()

    def _on_user_changed(self, _user: Any) -> None:
        self.sign_out()


_instance: GitHubSession | None = None
_instance_lock = threading.Lock()


def get_github_session() -> GitHubSession:
    """Return the process-wide GitHubSession bound to the auth SessionManager."""
    global _instance
    with _instance_lock:
        if _instance is None:
            from lightfall.auth.session import SessionManager

            _instance = GitHubSession(SessionManager.get_instance())
        return _instance


def scrub(text: str, *tokens: str) -> str:
    """Replace every non-empty token in text with ``***``.

    With no tokens given, scrubs the current credential's token.
    """
    if not tokens:
        # Use the raw held token, even if expired or another user's.
        tokens = (get_github_session()._held_token(),)
    for token in tokens:
        if token:
            text = text.replace(token, "***")
    return text
