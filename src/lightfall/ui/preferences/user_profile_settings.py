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
from lucid.settings.image_helpers import _fetch_qimage
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


_AVATAR_PX = 128

_ALLOWED_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


class UserProfileSettingsPlugin(SettingsPlugin):
    """Profile picture + identity preview."""

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._avatar_label: QLabel | None = None
        self._loaded_image_id: str | None = None
        self._load_future = None  # QThreadFuture
        self._upload_future = None
        self._remove_future = None

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
        """Render the current avatar from PreferencesManager's cache;
        subscribe for live updates so a change elsewhere re-renders this dialog."""
        from lucid.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        prefs.subscribe("profile_image_id", self._on_image_id_changed)
        image_id = prefs.get("profile_image_id", default=None)
        self._on_image_id_changed(image_id)

    def _on_image_id_changed(self, image_id: str | None) -> None:
        """Called both at dialog open and on any future prefs change."""
        from lucid.settings.user_settings_client import UserSettingsClient
        from lucid.utils.threads import QThreadFuture

        if not image_id:
            self._set_placeholder_avatar()
            self._loaded_image_id = None
            return
        if image_id == self._loaded_image_id:
            return

        client = UserSettingsClient.get_instance()
        self._load_future = QThreadFuture(
            _fetch_qimage,
            client,
            image_id,
            callback_slot=lambda qimg: self._on_image_ready(image_id, qimg),
            except_slot=lambda exc: self._on_image_error(exc),
        )
        self._load_future.start()

    def _on_image_ready(self, image_id: str, qimage) -> None:
        from PySide6.QtGui import QPixmap
        if qimage is None or qimage.isNull():
            self._set_placeholder_avatar()
            return
        pm = QPixmap.fromImage(qimage).scaled(
            _AVATAR_PX,
            _AVATAR_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._avatar_label is not None:
            self._avatar_label.setPixmap(pm)
        self._loaded_image_id = image_id

    def _on_image_error(self, exc: BaseException) -> None:
        logger.warning("Failed to load profile image: {}", exc)
        self._set_placeholder_avatar()
        self._loaded_image_id = None

    def save_settings(self) -> None:
        # Commit-on-action design: nothing to do here.
        return None

    def validate(self) -> list[str]:
        return []

    # ── Stubs the later tasks replace ────────────────────────────────────

    def _on_choose_clicked(self) -> None:
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        path_str, _ = QFileDialog.getOpenFileName(
            self._widget,
            "Choose profile image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif)",
        )
        if not path_str:
            return

        path = Path(path_str)
        ext = path.suffix.lower()
        mime = _ALLOWED_MIMES.get(ext)
        if mime is None:
            QMessageBox.warning(
                self._widget,
                "Unsupported file type",
                f"{ext or '(no extension)'} is not a supported image type. "
                "Please choose a PNG, JPEG, or GIF.",
            )
            return

        try:
            data = path.read_bytes()
        except OSError as e:
            QMessageBox.warning(
                self._widget, "Cannot read file", f"Could not read {path}: {e}"
            )
            return

        if len(data) > _MAX_IMAGE_BYTES:
            QMessageBox.warning(
                self._widget,
                "File too large",
                f"{path.name} is {len(data) // (1024*1024)} MB — limit is "
                f"{_MAX_IMAGE_BYTES // (1024*1024)} MB.",
            )
            return

        self._upload_and_set(data, mime)

    def _upload_and_set(self, data: bytes, mime: str) -> None:
        """Upload image blob; route the key/value write through PreferencesManager."""
        from PySide6.QtWidgets import QMessageBox

        from lucid.settings.user_settings_client import (
            UserSettingsClient,
            UserSettingsError,
        )
        from lucid.ui.preferences.manager import PreferencesManager
        from lucid.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()

        def work():
            # Blob upload stays direct on the client.
            return client.upload_image(data, mime)

        def on_ok(image_id: str):
            # Route the key/value write through PreferencesManager so that
            # observers (this dialog's subscription, the toolbar avatar) get
            # notified.
            PreferencesManager.get_instance().set("profile_image_id", image_id)

        def on_err(exc: BaseException):
            logger.warning("Profile image upload failed: {}", exc)
            QMessageBox.warning(
                self._widget,
                "Upload failed",
                f"Could not save profile image: {exc}",
            )

        self._upload_future = QThreadFuture(
            work,
            callback_slot=on_ok,
            except_slot=on_err,
        )
        self._upload_future.start()

    def _on_remove_clicked(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lucid.ui.preferences.manager import PreferencesManager

        try:
            PreferencesManager.get_instance().remove("profile_image_id")
        except Exception as exc:
            logger.warning("Profile image removal failed: {}", exc)
            QMessageBox.warning(
                self._widget,
                "Remove failed",
                f"Could not remove profile image: {exc}",
            )

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
