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
        """Fetch the current profile_image_id and render the avatar."""
        from lucid.settings.user_settings_client import UserSettingsClient
        from lucid.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()
        image_id = client.get("profile_image_id", default=None)
        if not image_id:
            self._set_placeholder_avatar()
            self._loaded_image_id = None
            return

        # Fetch the bytes on a worker thread, decode to QImage there,
        # then convert to QPixmap on the GUI thread (signal slot).
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
        """Upload image, set profile_image_id, refresh avatar."""
        from PySide6.QtWidgets import QMessageBox

        from lucid.settings.user_settings_client import (
            UserSettingsClient,
            UserSettingsError,
        )
        from lucid.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()

        def work():
            image_id = client.upload_image(data, mime)
            client.set("profile_image_id", image_id)
            return image_id

        def on_ok(image_id: str):
            # Re-trigger load to pull and display the new image.
            self.load_settings()

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

        from lucid.settings.user_settings_client import (
            UserSettingsClient,
            UserSettingsError,
        )
        from lucid.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()

        def work():
            try:
                client.delete("profile_image_id")
            except UserSettingsError as e:
                # If the setting wasn't there, treat as success.
                if "404" in str(e) or "Not Found" in str(e):
                    return
                raise

        def on_ok(_):
            self.load_settings()

        def on_err(exc):
            logger.warning("Profile image removal failed: {}", exc)
            QMessageBox.warning(
                self._widget,
                "Remove failed",
                f"Could not remove profile image: {exc}",
            )

        self._remove_future = QThreadFuture(
            work,
            callback_slot=on_ok,
            except_slot=on_err,
        )
        self._remove_future.start()

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


def _fetch_qimage(client, image_id: str):
    """Worker-thread function: download bytes via the client, decode to QImage.

    QImage is safe to construct off the GUI thread; QPixmap is not.
    """
    from PySide6.QtGui import QImage

    data, _ = client.download_image(image_id)
    img = QImage()
    img.loadFromData(data)
    return img
