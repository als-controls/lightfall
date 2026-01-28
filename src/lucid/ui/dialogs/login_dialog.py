"""Login dialog for LUCID authentication.

Provides a modal dialog for users to authenticate via Keycloak
or continue as a guest.
"""

from __future__ import annotations

import asyncio
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from lucid.auth.session import AuthState, SessionManager
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass


class LoginResult(Enum):
    """Result of the login dialog."""

    AUTHENTICATED = auto()  # User logged in successfully
    GUEST = auto()  # User chose to continue as guest
    CANCELLED = auto()  # User cancelled/closed the dialog


class LoginDialog(QDialog):
    """Modal dialog for user authentication.

    Presents options to login via Keycloak (opens browser) or
    continue as a guest with limited permissions.

    Signals:
        login_started: Emitted when login process begins.
        login_completed: Emitted with LoginResult when finished.

    Example:
        >>> dialog = LoginDialog(parent_window)
        >>> result = dialog.exec()
        >>> if result == QDialog.Accepted:
        ...     print(f"Login result: {dialog.result}")
    """

    login_started = Signal()
    login_completed = Signal(LoginResult)

    def __init__(
        self,
        parent: QWidget | None = None,
        title: str = "Login Required",
        message: str | None = None,
        allow_guest: bool = True,
        show_on_expiry: bool = False,
    ) -> None:
        """Initialize the login dialog.

        Args:
            parent: Parent widget.
            title: Dialog title.
            message: Custom message to display.
            allow_guest: Whether to show "Continue as Guest" option.
            show_on_expiry: Whether this is shown due to session expiry.
        """
        super().__init__(parent)
        self._allow_guest = allow_guest
        self._show_on_expiry = show_on_expiry
        self._login_result = LoginResult.CANCELLED
        self._session_manager = SessionManager.get_instance()
        self._login_thread: QThreadFuture | None = None

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(400)

        # Remove close button if guest not allowed
        if not allow_guest:
            self.setWindowFlags(
                self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
            )

        # Build UI
        self._setup_ui(message)

        # Connect to session manager
        self._session_manager.state_changed.connect(self._on_auth_state_changed)

    def _setup_ui(self, message: str | None) -> None:
        """Setup the dialog UI.

        Args:
            message: Custom message to display.
        """
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title/header
        if self._show_on_expiry:
            header_text = "Session Expired"
            default_message = (
                "Your session has expired. Please log in again to continue "
                "or continue as a guest with limited access."
            )
        else:
            header_text = "Welcome to LUCID"
            default_message = (
                "Please log in to access all features, or continue as a guest "
                "with limited access."
            )

        header = QLabel(header_text)
        header.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Message
        message_label = QLabel(message or default_message)
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message_label)

        layout.addSpacing(8)

        # Login button
        self._login_btn = QPushButton("Login with Keycloak")
        self._login_btn.setMinimumHeight(40)
        self._login_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0055aa;
            }
            QPushButton:pressed {
                background-color: #004488;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
            """
        )
        self._login_btn.clicked.connect(self._on_login_clicked)
        layout.addWidget(self._login_btn)

        # Progress indicator (hidden by default)
        self._progress_widget = QWidget()
        progress_layout = QHBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # Indeterminate
        self._progress_bar.setTextVisible(False)
        progress_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("Waiting for browser login...")
        progress_layout.addWidget(self._progress_label)

        self._progress_widget.setVisible(False)
        layout.addWidget(self._progress_widget)

        # Guest button
        if self._allow_guest:
            self._guest_btn = QPushButton("Continue as Guest")
            self._guest_btn.setMinimumHeight(36)
            self._guest_btn.clicked.connect(self._on_guest_clicked)
            layout.addWidget(self._guest_btn)
        else:
            self._guest_btn = None

        # Spacer
        layout.addItem(
            QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        # Info text
        info = QLabel(
            "Guest access provides read-only permissions.\n"
            "Full access requires authentication."
        )
        info.setStyleSheet("color: gray; font-size: 11px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

    @property
    def login_result(self) -> LoginResult:
        """Get the login result after dialog closes."""
        return self._login_result

    def _on_login_clicked(self) -> None:
        """Handle login button click."""
        self._login_btn.setEnabled(False)
        if self._guest_btn:
            self._guest_btn.setEnabled(False)
        self._progress_widget.setVisible(True)

        self.login_started.emit()
        logger.info("Starting Keycloak login flow")

        # Run async login in a background thread
        self._login_thread = QThreadFuture(
            self._do_login_sync,
            callback_slot=self._on_login_complete,
            except_slot=self._on_login_error,
            name="keycloak_login",
        )
        self._login_thread.start()

    def _do_login_sync(self) -> bool:
        """Perform the login synchronously (runs in background thread).

        Returns:
            True if login succeeded.
        """
        # Run the async login in this thread's event loop
        return asyncio.run(self._session_manager.login())

    def _on_login_complete(self, success: bool) -> None:
        """Handle login completion (called in main thread).

        Args:
            success: Whether login succeeded.
        """
        if success:
            self._login_result = LoginResult.AUTHENTICATED
            self.accept()
        else:
            # Login failed or was cancelled
            self._reset_ui()

    def _on_login_error(self, error: Exception) -> None:
        """Handle login error (called in main thread).

        Args:
            error: The exception that occurred.
        """
        logger.error("Login failed: {}", error)
        self._reset_ui()
        self._progress_label.setText("Login failed. Please try again.")

    def _reset_ui(self) -> None:
        """Reset UI after failed login attempt."""
        self._login_btn.setEnabled(True)
        if self._guest_btn:
            self._guest_btn.setEnabled(True)
        self._progress_widget.setVisible(False)

    def _on_guest_clicked(self) -> None:
        """Handle guest button click."""
        self._login_result = LoginResult.GUEST
        logger.info("User chose guest access")
        self.accept()

    @Slot(AuthState, AuthState)
    def _on_auth_state_changed(self, new_state: AuthState, old_state: AuthState) -> None:
        """Handle authentication state changes.

        Args:
            new_state: New auth state.
            old_state: Previous auth state.
        """
        if new_state == AuthState.AUTHENTICATED:
            self._login_result = LoginResult.AUTHENTICATED
            self.login_completed.emit(LoginResult.AUTHENTICATED)
            self.accept()
        elif new_state == AuthState.ERROR:
            self._reset_ui()
            self._progress_label.setText("Login failed. Please try again.")

    def closeEvent(self, event) -> None:
        """Handle dialog close."""
        if not self._allow_guest:
            # Don't allow closing without action
            event.ignore()
            return

        self._login_result = LoginResult.CANCELLED
        self.login_completed.emit(LoginResult.CANCELLED)
        super().closeEvent(event)

    def reject(self) -> None:
        """Handle dialog rejection (Escape key)."""
        if not self._allow_guest:
            return  # Don't allow rejection

        self._login_result = LoginResult.CANCELLED
        self.login_completed.emit(LoginResult.CANCELLED)
        super().reject()


def show_login_dialog(
    parent: QWidget | None = None,
    force: bool = False,
    on_expiry: bool = False,
) -> LoginResult:
    """Show the login dialog and return the result.

    Convenience function to display the login dialog.

    Args:
        parent: Parent widget.
        force: If True, don't allow guest access.
        on_expiry: If True, show expiry-specific messaging.

    Returns:
        The login result.
    """
    dialog = LoginDialog(
        parent=parent,
        allow_guest=not force,
        show_on_expiry=on_expiry,
    )
    dialog.exec()
    return dialog.login_result
