"""Qt building blocks for setup step pages: StepPage, Note, Tile, Spinner."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lightfall.ui.style import modern

if TYPE_CHECKING:
    from lightfall.setup.context import SetupContext

NOTE_ICONS = {"ok": "✓", "warn": "⚠", "error": "✕", "info": "ℹ"}


class Spinner(QWidget):
    """A small rotating arc for work in progress; animates only while started."""

    def __init__(self, parent: QWidget | None = None, size: int = 14) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._color = QColor(modern.current_tokens()["acc"])
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._color, 2))
        painter.drawArc(QRectF(self.rect()).adjusted(2, 2, -2, -2), -self._angle * 16, 270 * 16)
        painter.end()


class Note(QFrame):
    """Inline status: a tinted box with an icon (or a spinner) and one or two sentences."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("setupNote")
        self.setProperty("modern", "note")
        self.setProperty("kind", "info")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        self._spinner = Spinner(self)
        self._icon = QLabel(self)
        self._text = QLabel(self)
        self._text.setObjectName("setupNoteText")
        self._text.setWordWrap(True)
        row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._text, 1)
        self.hide()

    def show_note(self, kind: str, text: str) -> None:
        self.setProperty("kind", kind)
        self._spinner.stop()
        self._spinner.hide()
        self._icon.setText(NOTE_ICONS[kind])
        self._icon.show()
        self._text.setText(text)
        modern.repolish(self)
        self.show()

    def show_busy(self, text: str) -> None:
        """Spinner plus present-tense text ("Testing connection…")."""
        self.setProperty("kind", "info")
        self._icon.hide()
        self._spinner.show()
        self._spinner.start()
        self._text.setText(text)
        modern.repolish(self)
        self.show()

    def clear(self) -> None:
        self._spinner.stop()
        self.hide()

    def kind(self) -> str:
        return str(self.property("kind") or "")

    def text(self) -> str:
        return self._text.text()


class Tile(QFrame):
    """A selectable card: bold title, muted subtitle, optional avatar glyph."""

    clicked = Signal()

    def __init__(self, title: str, subtitle: str = "", avatar: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("modern", "tile")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(12)
        if avatar:
            badge = QLabel(avatar, self)
            badge.setProperty("tileRole", "avatar")
            badge.setFixedSize(34, 34)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(3)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("tileRole", "title")
        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setProperty("tileRole", "subtitle")
        self.subtitle_label.setWordWrap(True)
        column.addWidget(self.title_label)
        column.addWidget(self.subtitle_label)
        row.addLayout(column, 1)

    def set_text(self, title: str, subtitle: str) -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        modern.repolish(self)

    def is_selected(self) -> bool:
        return self.property("selected") == "true"

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StepPage(QWidget):
    """Base for a step's content. The wizard owns the header and footer: it asks the page
    for ``heading()``, ``primary_label()`` and ``skip_label()``, calls ``on_primary()``
    when the primary button is clicked, and validates/applies the step on ``completed``."""

    completed = Signal()
    skip_requested = Signal()
    primary_changed = Signal()

    def __init__(self, ctx: SetupContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        self.note = Note(self)
        self._layout.addWidget(self.note)

    def body(self) -> QVBoxLayout:
        return self._layout

    def heading(self) -> tuple[str, str]:
        return "", ""

    def primary_label(self) -> str:
        return "Continue"

    def primary_enabled(self) -> bool:
        return True

    def skip_label(self) -> str:
        return "Skip"

    def summary(self) -> str:
        return ""

    def on_primary(self) -> None:
        self.completed.emit()

    def on_enter(self) -> None:
        """The page became the current step."""

    def on_leave(self) -> None:
        """The user moved to another step; drop in-flight results."""

    def close_page(self) -> None:
        """The wizard is closing; stop timers and background work."""
