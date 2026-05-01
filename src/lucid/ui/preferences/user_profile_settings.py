# src/lucid/ui/preferences/user_profile_settings.py
"""Settings plugin for the per-user profile picture (and identity preview).

MVP scope: the user can view their identity (read-only labels), upload a
new profile image, or remove the current one. All actions commit
immediately on user input — there is no Apply/Cancel buffering — because
the work has non-trivial network side-effects whose rollback semantics
on Cancel would be ugly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


_AVATAR_PX = 128


class UserProfileSettingsPlugin(SettingsPlugin):
    """Profile picture + identity preview."""

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._avatar_label: QLabel | None = None

    @property
    def name(self) -> str:
        return "user_profile"

    @property
    def display_name(self) -> str:
        return "User Profile"

    @property
    def icon(self) -> "QIcon | None":
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 1  # under Appearance (0), above Login & Session (5)

    # ── Widget ───────────────────────────────────────────────────────────

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        outer.addLayout(row)

        # Avatar on the left
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_label.setStyleSheet(
            "QLabel { border: 1px solid palette(mid); border-radius: 8px; }"
        )
        self._set_placeholder_avatar()
        row.addWidget(self._avatar_label)

        # Identity labels on the right
        ident_box = QGroupBox("Identity")
        ident_form = QFormLayout(ident_box)

        username, display_name, email, orcid = self._read_identity()
        ident_form.addRow("Username:", QLabel(username))
        ident_form.addRow("Display name:", QLabel(display_name))
        ident_form.addRow("Email:", QLabel(email))
        if orcid:
            ident_form.addRow("ORCID:", QLabel(orcid))
        row.addWidget(ident_box, stretch=1)

        # Buttons row
        buttons = QHBoxLayout()
        self._choose_button = QPushButton("Choose Image…")
        self._remove_button = QPushButton("Remove Image")
        self._choose_button.clicked.connect(self._on_choose_clicked)
        self._remove_button.clicked.connect(self._on_remove_clicked)
        buttons.addWidget(self._choose_button)
        buttons.addWidget(self._remove_button)
        buttons.addStretch()
        outer.addLayout(buttons)

        outer.addWidget(QLabel(
            "Supported: PNG, JPEG, GIF · max 20 MB"
        ))
        outer.addStretch()

        self._widget = widget
        return widget

    # ── Lifecycle (noop bodies for now; later tasks fill in) ─────────────

    def load_settings(self) -> None:
        return None  # Task 12 implements

    def save_settings(self) -> None:
        # Commit-on-action design: nothing to do here.
        return None

    def validate(self) -> list[str]:
        return []

    # ── Stubs the later tasks replace ────────────────────────────────────

    def _on_choose_clicked(self) -> None:
        return None  # Task 13

    def _on_remove_clicked(self) -> None:
        return None  # Task 14

    # ── Helpers ──────────────────────────────────────────────────────────

    def _read_identity(self) -> tuple[str, str, str, str | None]:
        """Pull (username, display_name, email, orcid) from the current session."""
        try:
            from lucid.auth.session import SessionManager
            sess = SessionManager.get_instance().session
            if sess is None or sess.user is None:
                return ("(not logged in)", "", "", None)
            user = sess.user
            orcid = (user.attributes or {}).get("orcid") if hasattr(
                user, "attributes"
            ) else None
            return (
                user.username,
                user.display_name,
                user.email,
                orcid,
            )
        except Exception as e:
            logger.debug("Could not read identity from session: {}", e)
            return ("(unknown)", "", "", None)

    def _set_placeholder_avatar(self) -> None:
        """Show a blank silhouette placeholder."""
        if self._avatar_label is None:
            return
        pm = QPixmap(_AVATAR_PX, _AVATAR_PX)
        pm.fill(Qt.GlobalColor.lightGray)
        self._avatar_label.setPixmap(pm)
