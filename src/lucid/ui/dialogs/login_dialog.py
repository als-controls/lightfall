"""Login dialog for LUCID authentication.

Provides a modal dialog for users to authenticate via Keycloak
or a local development account.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.auth.session import AuthState, SessionManager
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lucid.auth.providers.base import AuthProvider


class LoginResult(Enum):
    """Result of the login dialog."""

    AUTHENTICATED = auto()  # User logged in successfully
    GUEST = auto()  # User chose to continue as guest
    CANCELLED = auto()  # User cancelled/closed the dialog


class LoginDialog(QDialog):
    """Modal dialog for user authentication.

    Presents options to login via Keycloak (opens browser) or
    use a local development account.

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
        self._current_provider: AuthProvider | None = None

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
        layout.setSpacing(12)
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
        self._message_label = QLabel(message or default_message)
        self._message_label.setWordWrap(True)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._message_label)

        layout.addSpacing(8)

        # Keycloak login button
        self._keycloak_btn = QPushButton("Login with Keycloak")
        self._keycloak_btn.setMinimumHeight(44)
        self._keycloak_btn.setStyleSheet(
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
        self._keycloak_btn.clicked.connect(self._on_keycloak_login)
        layout.addWidget(self._keycloak_btn)

        # Local login form (hidden by default)
        self._local_form = self._create_local_form()
        self._local_form.setVisible(False)
        layout.addWidget(self._local_form)

        # Progress indicator (hidden by default)
        self._progress_widget = QWidget()
        progress_layout = QHBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # Indeterminate
        self._progress_bar.setTextVisible(False)
        progress_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("Logging in...")
        progress_layout.addWidget(self._progress_label)

        self._progress_widget.setVisible(False)
        layout.addWidget(self._progress_widget)

        # Error label (hidden by default)
        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #cc0000; font-size: 12px;")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        # Guest button
        if self._allow_guest:
            self._guest_btn = QPushButton("Continue as Guest")
            self._guest_btn.setMinimumHeight(36)
            self._guest_btn.clicked.connect(self._on_guest_clicked)
            layout.addWidget(self._guest_btn)
        else:
            self._guest_btn = None

        # Link to switch to local login (in main layout, below guest button)
        self._local_link = QPushButton("Use local account instead")
        self._local_link.setFlat(True)
        self._local_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._local_link.setStyleSheet(
            """
            QPushButton {
                color: #0066cc;
                text-decoration: underline;
                border: none;
                background: transparent;
                font-size: 11px;
                padding: 4px;
            }
            QPushButton:hover {
                color: #004488;
            }
            """
        )
        self._local_link.clicked.connect(self._show_local_page)
        layout.addWidget(self._local_link, alignment=Qt.AlignmentFlag.AlignCenter)

        # Info text
        self._info_label = QLabel(
            "Guest access provides read-only permissions."
        )
        self._info_label.setStyleSheet("color: gray; font-size: 10px;")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

    def _create_local_form(self) -> QWidget:
        """Create the local login form with username/password fields."""
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Username/password form
        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText("Enter username")
        self._username_edit.setMinimumHeight(32)
        form_layout.addRow("Username:", self._username_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setPlaceholderText("Enter password")
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setMinimumHeight(32)
        self._password_edit.returnPressed.connect(self._on_local_login)
        form_layout.addRow("Password:", self._password_edit)

        layout.addLayout(form_layout)

        # Local login button
        self._local_login_btn = QPushButton("Login")
        self._local_login_btn.setMinimumHeight(40)
        self._local_login_btn.setStyleSheet(
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
        self._local_login_btn.clicked.connect(self._on_local_login)
        layout.addWidget(self._local_login_btn)

        # Link to go back to Keycloak
        self._keycloak_link = QPushButton("Back to Keycloak login")
        self._keycloak_link.setFlat(True)
        self._keycloak_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keycloak_link.setStyleSheet(
            """
            QPushButton {
                color: #0066cc;
                text-decoration: underline;
                border: none;
                background: transparent;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #004488;
            }
            """
        )
        self._keycloak_link.clicked.connect(self._show_keycloak_page)
        layout.addWidget(self._keycloak_link, alignment=Qt.AlignmentFlag.AlignCenter)

        # Dev hint
        hint = QLabel("Dev accounts: admin/admin, user/user, operator/operator")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        return form

    def _show_keycloak_page(self) -> None:
        """Switch to Keycloak login view."""
        self._keycloak_btn.setVisible(True)
        self._local_form.setVisible(False)
        self._local_link.setVisible(True)
        self._error_label.setVisible(False)

    def _show_local_page(self) -> None:
        """Switch to local login view."""
        self._keycloak_btn.setVisible(False)
        self._local_form.setVisible(True)
        self._local_link.setVisible(False)
        self._error_label.setVisible(False)
        self._username_edit.setFocus()

    @property
    def login_result(self) -> LoginResult:
        """Get the login result after dialog closes."""
        return self._login_result

    def _on_keycloak_login(self) -> None:
        """Handle Keycloak login button click."""
        self._keycloak_btn.setEnabled(False)
        self._local_link.setVisible(False)
        if self._guest_btn:
            self._guest_btn.setEnabled(False)
        self._progress_label.setText("Waiting for browser login...")
        self._progress_widget.setVisible(True)
        self._error_label.setVisible(False)

        self.login_started.emit()
        logger.info("Starting Keycloak login flow")

        # Run Keycloak login in background thread
        self._login_thread = QThreadFuture(
            self._do_keycloak_login,
            callback_slot=self._on_login_complete,
            except_slot=self._on_login_error,
            name="keycloak_login",
        )
        self._login_thread.start()

    def _do_keycloak_login(self) -> bool:
        """Perform Keycloak login (runs in background thread)."""
        from lucid.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig
        from lucid.config import ConfigManager
        from lucid.core import NCSApplication

        # Get Keycloak config
        app = NCSApplication.get_instance()
        config: ConfigManager = app.services.get(ConfigManager)
        auth_config = config.model.auth.provider

        kc_config = KeycloakConfig(
            server_url=auth_config.server_url,
            realm=auth_config.realm,
            client_id=auth_config.client_id,
            client_secret=auth_config.client_secret or None,
            redirect_uri=auth_config.redirect_uri,
        )

        provider = KeycloakAuthProvider(kc_config)
        self._current_provider = provider

        # Run async authenticate
        session = asyncio.run(provider.authenticate())

        if session:
            # Set session in SessionManager
            self._session_manager._session = session
            self._session_manager._set_state(AuthState.AUTHENTICATED)
            self._session_manager.user_changed.emit(session.user)
            return True

        return False

    def _on_local_login(self) -> None:
        """Handle local login button click."""
        username = self._username_edit.text().strip()
        password = self._password_edit.text()

        if not username or not password:
            self._show_error("Please enter username and password")
            return

        self._local_login_btn.setEnabled(False)
        self._username_edit.setEnabled(False)
        self._password_edit.setEnabled(False)
        self._keycloak_link.setEnabled(False)
        if self._guest_btn:
            self._guest_btn.setEnabled(False)
        self._progress_label.setText("Logging in...")
        self._progress_widget.setVisible(True)
        self._error_label.setVisible(False)

        self.login_started.emit()
        logger.info("Starting local login flow")

        # Run local login in background thread
        self._login_thread = QThreadFuture(
            self._do_local_login,
            username,
            password,
            callback_slot=self._on_login_complete,
            except_slot=self._on_login_error,
            name="local_login",
        )
        self._login_thread.start()

    def _do_local_login(self, username: str, password: str) -> bool:
        """Perform local login (runs in background thread)."""
        from lucid.auth.providers.local import LocalAuthProvider
        from lucid.ui.preferences.login_settings import LoginSettingsProvider

        duration = LoginSettingsProvider.get_session_duration()
        provider = LocalAuthProvider(session_duration=duration)
        self._current_provider = provider

        # Run async authenticate
        session = asyncio.run(provider.authenticate(username=username, password=password))

        if session:
            # Set session in SessionManager
            self._session_manager._session = session
            self._session_manager._set_state(AuthState.AUTHENTICATED)
            self._session_manager.user_changed.emit(session.user)
            return True

        return False

    def _on_login_complete(self, success: bool) -> None:
        """Handle login completion (called in main thread).

        Args:
            success: Whether login succeeded.
        """
        if success:
            self._login_result = LoginResult.AUTHENTICATED
            self.accept()
        else:
            self._reset_ui()
            if self._local_form.isVisible():  # Local page
                self._show_error("Invalid username or password")
            else:
                self._show_error("Login failed or was cancelled")

    def _on_login_error(self, error: Exception) -> None:
        """Handle login error (called in main thread).

        Args:
            error: The exception that occurred.
        """
        logger.error("Login failed: {}", error)
        self._reset_ui()
        self._show_error(f"Login failed: {error}")

    def _reset_ui(self) -> None:
        """Reset UI after failed login attempt."""
        # Keycloak
        self._keycloak_btn.setEnabled(True)

        # Local form
        self._local_login_btn.setEnabled(True)
        self._username_edit.setEnabled(True)
        self._password_edit.setEnabled(True)
        self._password_edit.clear()
        self._keycloak_link.setEnabled(True)

        # Common
        if self._guest_btn:
            self._guest_btn.setEnabled(True)
        self._local_link.setVisible(self._keycloak_btn.isVisible())
        self._progress_widget.setVisible(False)

        # Focus appropriate field
        if self._local_form.isVisible():
            self._password_edit.setFocus()

    def _show_error(self, message: str) -> None:
        """Show an error message in the dialog.

        Args:
            message: Error message to display.
        """
        self._error_label.setText(message)
        self._error_label.setVisible(True)

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
            self._show_error("Login failed. Please try again.")

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
