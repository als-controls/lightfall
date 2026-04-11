"""TheaterProxy — QStackedWidget wrapper for theater mode."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QToolButton, QWidget

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover
    qta = None


class TheaterProxy(QStackedWidget):
    """Wraps a widget for theater mode expansion.

    Page 0: target widget (normal display).
    Page 1: placeholder shown while the widget is on the overlay.
    """

    expand_requested = Signal()

    def __init__(self, widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = widget
        self.setObjectName("TheaterProxy")

        # Page 0 — target widget
        self.addWidget(widget)

        # Page 1 — placeholder
        self._placeholder = QLabel("Expanded")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("TheaterPlaceholder")
        self.addWidget(self._placeholder)

        self.setCurrentIndex(0)

        # Hover expand button (hidden by default, configured in Task 2)
        self._expand_btn = QToolButton(self)
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._expand_btn.setObjectName("TheaterExpandButton")
        self._expand_btn.setVisible(False)
        self._expand_btn.clicked.connect(self.expand_requested.emit)
        self._expand_btn.setStyleSheet(
            "QToolButton { background: rgba(0,0,0,120); border: none; "
            "border-radius: 4px; padding: 2px; }"
            "QToolButton:hover { background: rgba(0,0,0,180); }"
        )
        if qta is not None:
            try:
                self._expand_btn.setIcon(
                    qta.icon("mdi6.arrow-expand-all", color="#e0e0e0")
                )
            except Exception:
                self._expand_btn.setText("\u26f6")
        else:
            self._expand_btn.setText("\u26f6")

        # Register with theater manager
        from lucid.ui.theater.manager import theater_manager

        theater_manager.register(self)

    @property
    def target_widget(self) -> QWidget:
        """The widget wrapped by this proxy."""
        return self._target

    def take_widget(self) -> QWidget:
        """Remove the target widget and show the placeholder."""
        self.removeWidget(self._target)
        self.setCurrentIndex(0)  # placeholder is now index 0
        return self._target

    def return_widget(self, widget: QWidget) -> None:
        """Return the target widget and show it."""
        self.insertWidget(0, widget)
        self.setCurrentIndex(0)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self.currentWidget() is self._target:
            self._expand_btn.setVisible(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._expand_btn.setVisible(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        margin = 4
        self._expand_btn.move(
            self.width() - self._expand_btn.width() - margin,
            margin,
        )
