"""Dismissable warning banner widget for persistent warnings.

Provides a warning banner that can be permanently dismissed by the user,
with the dismissal state saved to preferences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ncs.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class DismissableWarningBanner(QFrame):
    """Warning banner with 'Don't show again' option.

    A styled warning banner that appears at the top of a window to alert
    the user about important configuration issues. The user can dismiss
    it temporarily (closes after timeout) or permanently (via button).

    Signals:
        permanently_dismissed: Emitted with warning_id when user clicks
            "Don't show again". Connect this to save the preference.
        closed: Emitted when the banner is closed (any reason).

    Example:
        >>> banner = DismissableWarningBanner(
        ...     warning_id="jetbrains_toolbox",
        ...     title="JetBrains Toolbox Required",
        ...     message="Install JetBrains Toolbox for PyCharm code links to work",
        ...     parent=main_window
        ... )
        >>> banner.permanently_dismissed.connect(
        ...     lambda wid: prefs.set("suppress_" + wid + "_warning", True)
        ... )
        >>> banner.show()
    """

    permanently_dismissed = Signal(str)  # Emits warning_id
    closed = Signal()

    # Auto-hide timeout in milliseconds
    AUTO_HIDE_TIMEOUT = 15000  # 15 seconds

    def __init__(
        self,
        warning_id: str,
        title: str,
        message: str,
        parent: QWidget | None = None,
        auto_hide: bool = True,
    ) -> None:
        """Initialize the warning banner.

        Args:
            warning_id: Unique identifier for this warning (used in signal).
            title: Bold title text for the warning.
            message: Detailed message text.
            parent: Parent widget.
            auto_hide: If True, automatically hide after AUTO_HIDE_TIMEOUT.
        """
        super().__init__(parent)
        self._warning_id = warning_id
        self._auto_hide = auto_hide
        self._auto_hide_timer: QTimer | None = None

        self._setup_ui(title, message)
        self._setup_style()

        if auto_hide:
            self._setup_auto_hide()

        logger.debug("Created warning banner: {}", warning_id)

    def _setup_ui(self, title: str, message: str) -> None:
        """Create the banner UI layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        # Warning icon (using unicode character)
        icon_label = QLabel("\u26a0")  # Warning sign
        icon_label.setStyleSheet("font-size: 18px;")
        layout.addWidget(icon_label)

        # Text content
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        title_label = QLabel(f"<b>{title}</b>")
        title_label.setWordWrap(True)
        text_layout.addWidget(title_label)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("color: #666;")
        text_layout.addWidget(message_label)

        layout.addLayout(text_layout, stretch=1)

        # Buttons
        button_layout = QVBoxLayout()
        button_layout.setSpacing(4)

        self._dismiss_btn = QPushButton("Don't show again")
        self._dismiss_btn.setFixedWidth(120)
        self._dismiss_btn.clicked.connect(self._on_permanent_dismiss)
        button_layout.addWidget(self._dismiss_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.setFixedWidth(120)
        self._close_btn.clicked.connect(self._on_close)
        button_layout.addWidget(self._close_btn)

        layout.addLayout(button_layout)

    def _setup_style(self) -> None:
        """Apply warning styling to the banner."""
        self.setStyleSheet("""
            DismissableWarningBanner {
                background-color: #fff3cd;
                border: 1px solid #ffc107;
                border-radius: 4px;
            }
            DismissableWarningBanner QLabel {
                color: #856404;
            }
            DismissableWarningBanner QPushButton {
                background-color: #ffc107;
                border: 1px solid #e0a800;
                border-radius: 3px;
                padding: 4px 8px;
                color: #212529;
            }
            DismissableWarningBanner QPushButton:hover {
                background-color: #e0a800;
            }
            DismissableWarningBanner QPushButton:pressed {
                background-color: #d39e00;
            }
        """)

    def _setup_auto_hide(self) -> None:
        """Setup the auto-hide timer."""
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._on_close)
        self._auto_hide_timer.start(self.AUTO_HIDE_TIMEOUT)

    def _on_permanent_dismiss(self) -> None:
        """Handle permanent dismiss button click."""
        logger.info("Warning permanently dismissed: {}", self._warning_id)
        self.permanently_dismissed.emit(self._warning_id)
        self._close()

    def _on_close(self) -> None:
        """Handle close button click or auto-hide timeout."""
        logger.debug("Warning banner closed: {}", self._warning_id)
        self._close()

    def _close(self) -> None:
        """Close and clean up the banner."""
        if self._auto_hide_timer is not None:
            self._auto_hide_timer.stop()

        self.closed.emit()
        self.hide()
        self.deleteLater()

    @property
    def warning_id(self) -> str:
        """Get the warning identifier."""
        return self._warning_id
