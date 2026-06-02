"""Small clickable avatar widget for the menubar corner.

Subscribes to `profile_image_id` on PreferencesManager; renders a
circular crop of the current profile picture, falling back to a
generic placeholder when unset. Click emits `clicked` so a host (the
mainwindow) can open the preferences dialog.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from lightfall.settings.image_helpers import _fetch_qimage
from lightfall.settings.user_settings_client import UserSettingsClient
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture


_AVATAR_PX = 28
_PLACEHOLDER_COLOR = QColor(140, 140, 140)


class ProfileAvatarWidget(QWidget):
    """Menubar-corner avatar. Reactive on `profile_image_id` changes."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(_AVATAR_PX, _AVATAR_PX))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("User profile")

        self._pixmap: QPixmap | None = None
        self._loaded_image_id: str | None = None
        self._fetching_image_id: str | None = None

        prefs = PreferencesManager.get_instance()
        prefs.subscribe("profile_image_id", self._on_image_id_changed)

        # Seed from cache (may be None if refresh hasn't run yet).
        initial = prefs.get("profile_image_id")
        if initial:
            self._kick_off_fetch(initial)

    # ── Qt overrides ────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)

        if self._pixmap is not None:
            painter.drawPixmap(rect, self._pixmap)
        else:
            painter.fillRect(rect, QBrush(_PLACEHOLDER_COLOR))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    # ── Subscription handler ────────────────────────────────────────

    def _on_image_id_changed(self, new_id: Any) -> None:
        if new_id and (new_id == self._loaded_image_id or new_id == self._fetching_image_id):
            return
        if not new_id:
            self._pixmap = None
            self._loaded_image_id = None
            self.update()
            return
        self._kick_off_fetch(new_id)

    # ── Worker thread plumbing ──────────────────────────────────────

    def _kick_off_fetch(self, image_id: str) -> None:
        self._fetching_image_id = image_id
        client = UserSettingsClient.get_instance()

        def work():
            return _fetch_qimage(client, image_id)

        # Note: QThreadFuture registers itself with ThreadManager so it
        # is retained while running; we don't need a local ref.
        QThreadFuture(
            work,
            callback_slot=lambda qimg: self._on_image_ready(image_id, qimg),
            except_slot=lambda exc: self._on_image_error(image_id, exc),
        ).start()

    def _on_image_ready(self, image_id: str, qimage) -> None:
        if self._fetching_image_id == image_id:
            self._fetching_image_id = None
        if qimage is None or qimage.isNull():
            self._pixmap = None
            self._loaded_image_id = None
            self.update()
            return
        pm = QPixmap.fromImage(qimage).scaled(
            _AVATAR_PX,
            _AVATAR_PX,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._pixmap = pm
        self._loaded_image_id = image_id
        self.update()

    def _on_image_error(self, image_id: str, exc: BaseException) -> None:
        logger.warning("Failed to load profile image {!r}: {}", image_id, exc)
        if self._fetching_image_id == image_id:
            self._fetching_image_id = None
        self._pixmap = None
        self._loaded_image_id = None
        self.update()
