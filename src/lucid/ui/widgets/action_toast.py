"""Action toast widget for NCS.

Provides a toast-like notification with a clickable action link.
Unlike pyqttoast which doesn't support buttons, this widget provides
interactive notifications for actions like "Session Settings".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.theme import ThemeManager

if TYPE_CHECKING:
    pass


class ActionToast(QWidget):
    """Toast-like notification with a clickable action link.

    Provides an interactive toast that can trigger an action when clicked.
    Positioned at bottom-right like standard toasts and styled to match
    the current theme.

    Signals:
        action_clicked: Emitted when the action link is clicked.
        closed: Emitted when the toast is closed (manually or by timeout).

    Example:
        >>> toast = ActionToast(
        ...     title="Logged In",
        ...     text="Session expires at 3:00 PM",
        ...     action_text="Session Settings",
        ...     parent=main_window,
        ... )
        >>> toast.action_clicked.connect(open_settings)
        >>> toast.show()
    """

    action_clicked = Signal()
    closed = Signal()

    # Default configuration
    DEFAULT_DURATION = 8000  # 8 seconds (longer than standard toasts)
    MARGIN = 20  # Margin from window edges
    WIDTH = 320
    FADE_DURATION = 200

    def __init__(
        self,
        title: str,
        text: str = "",
        action_text: str = "",
        duration: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the action toast.

        Args:
            title: Toast title text.
            text: Toast body text.
            action_text: Text for the action link (if empty, no action shown).
            duration: Auto-dismiss duration in ms (None for default).
            parent: Parent widget for positioning.
        """
        super().__init__(parent)
        self._duration = duration if duration is not None else self.DEFAULT_DURATION
        self._theme_manager = ThemeManager.get_instance()

        # Window flags for overlay behavior
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(self.WIDTH)

        # Setup UI
        self._setup_ui(title, text, action_text)
        self._apply_theme()

        # Connect to theme changes
        self._theme_manager.theme_changed.connect(self._apply_theme)

        # Auto-dismiss timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

        # Fade animation
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._container.setGraphicsEffect(self._opacity_effect)
        self._fade_animation = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_animation.setDuration(self.FADE_DURATION)

    def _setup_ui(self, title: str, text: str, action_text: str) -> None:
        """Setup the toast UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Container for styling
        self._container = QWidget()
        self._container.setObjectName("ActionToastContainer")
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(16, 12, 16, 12)
        container_layout.setSpacing(4)

        # Header row with title and close button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        # Info icon (using unicode)
        icon_label = QLabel("\u2139")  # Information source symbol
        icon_label.setObjectName("ActionToastIcon")
        header_layout.addWidget(icon_label)

        # Title
        self._title_label = QLabel(title)
        self._title_label.setObjectName("ActionToastTitle")
        header_layout.addWidget(self._title_label, 1)

        # Close button
        close_btn = QPushButton("\u00d7")  # Multiplication sign (x)
        close_btn.setObjectName("ActionToastClose")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close_clicked)
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Body text
        if text:
            self._text_label = QLabel(text)
            self._text_label.setObjectName("ActionToastText")
            self._text_label.setWordWrap(True)
            container_layout.addWidget(self._text_label)
        else:
            self._text_label = None

        # Action link
        if action_text:
            self._action_btn = QPushButton(action_text)
            self._action_btn.setObjectName("ActionToastAction")
            self._action_btn.setFlat(True)
            self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._action_btn.clicked.connect(self._on_action_clicked)
            container_layout.addWidget(
                self._action_btn, alignment=Qt.AlignmentFlag.AlignLeft
            )
        else:
            self._action_btn = None

        layout.addWidget(self._container)

    def _apply_theme(self) -> None:
        """Apply theme-appropriate styling."""
        is_dark = self._theme_manager.is_dark

        if is_dark:
            bg_color = "#2d2d30"
            border_color = "#3e8bff"
            title_color = "#e0e0e0"
            text_color = "#b0b0b0"
            action_color = "#3e8bff"
            action_hover = "#5ea3ff"
            close_color = "#808080"
            close_hover = "#e0e0e0"
            icon_color = "#3e8bff"
        else:
            bg_color = "#ffffff"
            border_color = "#0066cc"
            title_color = "#1a1a1a"
            text_color = "#666666"
            action_color = "#0066cc"
            action_hover = "#004488"
            close_color = "#999999"
            close_hover = "#333333"
            icon_color = "#0066cc"

        self._container.setStyleSheet(f"""
            QWidget#ActionToastContainer {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QLabel#ActionToastIcon {{
                color: {icon_color};
                font-size: 16px;
                font-weight: bold;
            }}
            QLabel#ActionToastTitle {{
                color: {title_color};
                font-size: 13px;
                font-weight: bold;
            }}
            QLabel#ActionToastText {{
                color: {text_color};
                font-size: 12px;
            }}
            QPushButton#ActionToastAction {{
                color: {action_color};
                text-decoration: underline;
                border: none;
                background: transparent;
                font-size: 12px;
                padding: 2px 0px;
                text-align: left;
            }}
            QPushButton#ActionToastAction:hover {{
                color: {action_hover};
            }}
            QPushButton#ActionToastClose {{
                color: {close_color};
                border: none;
                background: transparent;
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton#ActionToastClose:hover {{
                color: {close_hover};
            }}
        """)

    def showEvent(self, event) -> None:
        """Handle show event - position and start timer."""
        super().showEvent(event)
        self._position_toast()
        self._opacity_effect.setOpacity(1.0)
        if self._duration > 0:
            self._timer.start(self._duration)

    def _position_toast(self) -> None:
        """Position the toast at bottom-right of parent or screen."""
        self.adjustSize()

        # Get the reference rect (parent window or screen)
        if self.parent():
            parent_widget = self.parent()
            # Map parent's bottom-right to global coordinates
            parent_rect = parent_widget.rect()
            bottom_right = parent_widget.mapToGlobal(
                QPoint(parent_rect.right(), parent_rect.bottom())
            )
            x = bottom_right.x() - self.width() - self.MARGIN
            y = bottom_right.y() - self.height() - self.MARGIN
        else:
            # Use screen geometry
            screen = QApplication.primaryScreen()
            if screen:
                screen_rect = screen.availableGeometry()
                x = screen_rect.right() - self.width() - self.MARGIN
                y = screen_rect.bottom() - self.height() - self.MARGIN
            else:
                x = 100
                y = 100

        self.move(x, y)

    def _on_action_clicked(self) -> None:
        """Handle action link click."""
        self._timer.stop()
        self.action_clicked.emit()
        self._close_toast()

    def _on_close_clicked(self) -> None:
        """Handle close button click."""
        self._timer.stop()
        self._close_toast()

    def _fade_out(self) -> None:
        """Fade out the toast before closing."""
        self._fade_animation.setStartValue(1.0)
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.finished.connect(self._close_toast)
        self._fade_animation.start()

    def _close_toast(self) -> None:
        """Close and clean up the toast."""
        self.closed.emit()
        self.close()
        self.deleteLater()
