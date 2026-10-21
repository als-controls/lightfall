"""Queue of spoken prompts waiting for the agent, plus the strip that shows them.

``VoiceQueue`` is the model: a FIFO of ``QueuedPrompt`` with a sticky
``paused`` flag (set after a turn ends in error) and a ``held`` flag (set
while a rebuilt session has not yet confirmed its conversation).
``VoiceQueueStrip`` is the view: it sits directly above the chat input row, lists each queued prompt as
a dimmed label with a ✕, and when paused shows "Paused after error" with a
"Send queued" button.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.theme import scaled_pt

PAUSED_TEXT = "Paused after error"
SEND_QUEUED_TEXT = "Send queued"


@dataclass(frozen=True)
class QueuedPrompt:
    """One prompt waiting to be sent."""

    id: str
    text: str


class VoiceQueue(QObject):
    """FIFO of prompts waiting for the agent to be free.

    ``changed`` fires on every mutation, including pause/resume transitions.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[QueuedPrompt] = []
        self._paused = False
        self._held = False

    def items(self) -> list[QueuedPrompt]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def push(self, text: str) -> str:
        item = QueuedPrompt(uuid.uuid4().hex, text)
        self._items.append(item)
        self.changed.emit()
        return item.id

    def remove(self, id: str) -> None:  # noqa: A002 - interface name
        before = len(self._items)
        self._items = [it for it in self._items if it.id != id]
        if len(self._items) != before:
            self.changed.emit()

    def pop_next(self) -> QueuedPrompt | None:
        if not self._items:
            return None
        item = self._items.pop(0)
        self.changed.emit()
        return item

    def clear(self) -> int:
        """Drop every queued prompt (the pause flag is kept); returns how many."""
        n = len(self._items)
        if n:
            self._items = []
            self.changed.emit()
        return n

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        if not self._paused:
            self._paused = True
            self.changed.emit()

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self.changed.emit()

    @property
    def held(self) -> bool:
        """Handed across a widget rebuild and waiting for the new agent to
        confirm it resumed the same conversation; nothing drains meanwhile."""
        return self._held

    def hold(self) -> None:
        if not self._held:
            self._held = True
            self.changed.emit()

    def release(self) -> None:
        if self._held:
            self._held = False
            self.changed.emit()


class VoiceQueueStrip(QWidget):
    """View of a ``VoiceQueue``; hidden while the queue is empty.

    Emits ``send_queued_requested`` when the user presses "Send queued".
    """

    send_queued_requested = Signal()

    def __init__(self, queue: VoiceQueue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("voiceQueueStrip")
        self._queue: VoiceQueue | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._frame = QFrame()
        self._frame.setObjectName("voiceQueueCard")
        self._frame.setFrameShape(QFrame.Shape.NoFrame)
        self._frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        outer.addWidget(self._frame)

        self._layout = QVBoxLayout(self._frame)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        # Paused row: label + "Send queued" button.
        self._paused_row = QWidget()
        paused_layout = QHBoxLayout(self._paused_row)
        paused_layout.setContentsMargins(0, 0, 0, 0)
        self.paused_label = QLabel(PAUSED_TEXT)
        self.paused_label.setObjectName("voiceQueuePausedLabel")
        paused_layout.addWidget(self.paused_label)
        paused_layout.addStretch(1)
        self.send_queued_button = QPushButton(SEND_QUEUED_TEXT)
        self.send_queued_button.setObjectName("voiceQueueSendQueued")
        self.send_queued_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_queued_button.clicked.connect(self.send_queued_requested)
        paused_layout.addWidget(self.send_queued_button)
        self._layout.addWidget(self._paused_row)

        self._items_box = QWidget()
        self._items_layout = QVBoxLayout(self._items_box)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.setSpacing(2)
        self._layout.addWidget(self._items_box)

        self._apply_style()
        self.set_queue(queue)

    def set_queue(self, queue: VoiceQueue) -> None:
        if self._queue is not None:
            try:
                self._queue.changed.disconnect(self._rebuild)
            except (RuntimeError, TypeError):
                pass
        self._queue = queue
        queue.changed.connect(self._rebuild)
        self._rebuild()

    def _apply_style(self) -> None:
        # Same box as the transcript cards (``_create_card``), with a muted accent.
        base = self.palette().color(QPalette.ColorRole.Base)
        is_dark = base.lightness() < 128
        bg = "#2a2a2a" if is_dark else "#f5f5f5"
        self._frame.setStyleSheet(
            f"QFrame#voiceQueueCard {{ background: {bg}; border-left: 4px solid #888; "
            f"border-radius: 4px; padding: 6px 12px; }}"
        )
        self.paused_label.setStyleSheet(
            f"font-weight: bold; font-size: {scaled_pt(8)}pt; color: #e0a030; "
            f"letter-spacing: 1px;"
        )

    def _muted_color(self) -> str:
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.Text)
        bg = pal.color(QPalette.ColorRole.Base)
        r = int(text.red() * 0.6 + bg.red() * 0.4)
        g = int(text.green() * 0.6 + bg.green() * 0.4)
        b = int(text.blue() * 0.6 + bg.blue() * 0.4)
        return f"rgb({r}, {g}, {b})"

    def _rebuild(self) -> None:
        while self._items_layout.count():
            item = self._items_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        queue = self._queue
        items = queue.items() if queue is not None else []
        muted = self._muted_color()
        for prompt in items:
            row = QWidget()
            row.setObjectName("voiceQueueItem")
            row.setProperty("prompt_id", prompt.id)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(prompt.text)
            label.setObjectName("voiceQueueItemLabel")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet(f"color: {muted}; font-style: italic;")
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            row_layout.addWidget(label, 1)
            remove = QToolButton()
            remove.setObjectName("voiceQueueItemRemove")
            remove.setText("✕")
            remove.setToolTip("Remove from queue")
            remove.setAutoRaise(True)
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(
                lambda _c=False, pid=prompt.id: self._queue and self._queue.remove(pid)
            )
            row_layout.addWidget(remove)
            self._items_layout.addWidget(row)

        paused = bool(queue is not None and queue.paused)
        self._paused_row.setVisible(paused)
        self.setVisible(bool(items))

    def item_rows(self) -> list[QWidget]:
        """The live per-prompt rows, in queue order (for tests / accessibility)."""
        rows = []
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if w is not None:
                rows.append(w)
        return rows
