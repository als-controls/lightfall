"""Trust management for IPC application connections.

Provides session-scoped tracking of which applications have been approved or
denied, and a Qt dialog for prompting the user when an unknown app connects.
"""

from __future__ import annotations

import enum
import threading

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

__all__ = ["TrustDialog", "TrustManager", "TrustState"]


# ---------------------------------------------------------------------------
# TrustState
# ---------------------------------------------------------------------------


class TrustState(enum.Enum):
    UNKNOWN = "unknown"
    APPROVED = "approved"
    DENIED = "denied"


# ---------------------------------------------------------------------------
# TrustManager
# ---------------------------------------------------------------------------


class TrustManager:
    """Thread-safe, session-scoped store of application trust decisions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trusted: set[str] = set()
        self._denied: set[str] = set()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def approve(self, app_name: str) -> None:
        """Mark *app_name* as trusted for this session."""
        with self._lock:
            self._trusted.add(app_name)
            self._denied.discard(app_name)

    def deny(self, app_name: str) -> None:
        """Mark *app_name* as denied for this session."""
        with self._lock:
            self._denied.add(app_name)
            self._trusted.discard(app_name)

    def revoke(self, app_name: str) -> None:
        """Remove *app_name* from the trusted set without adding it to denied."""
        with self._lock:
            self._trusted.discard(app_name)

    def clear(self) -> None:
        """Reset all trust state."""
        with self._lock:
            self._trusted.clear()
            self._denied.clear()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def check(self, app_name: str) -> TrustState:
        """Return APPROVED, DENIED, or UNKNOWN for *app_name*."""
        with self._lock:
            if app_name in self._trusted:
                return TrustState.APPROVED
            if app_name in self._denied:
                return TrustState.DENIED
            return TrustState.UNKNOWN

    def is_trusted(self, app_name: str) -> bool:
        with self._lock:
            return app_name in self._trusted

    def is_denied(self, app_name: str) -> bool:
        with self._lock:
            return app_name in self._denied

    @property
    def trusted_apps(self) -> set[str]:
        """Return a copy of the currently trusted application names."""
        with self._lock:
            return set(self._trusted)


# ---------------------------------------------------------------------------
# TrustDialog
# ---------------------------------------------------------------------------


class TrustDialog(QDialog):
    """Modal dialog that asks the user whether to trust a connecting application.

    Accepted (return code ``QDialog.Accepted``) means the user clicked
    "Trust for this session".  Rejected means they clicked "Deny".
    """

    def __init__(
        self,
        app_name: str,
        app_version: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trust Application?")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        version_str = f" v{app_version}" if app_version else ""
        message = (
            f"{app_name}{version_str} wants to connect to Lightfall.\n"
            "Trust this application for this session?"
        )
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText(
            "Trust for this session"
        )
        buttons.button(QDialogButtonBox.StandardButton.No).setText("Deny")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
