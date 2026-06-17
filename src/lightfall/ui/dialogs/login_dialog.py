"""Login dialog for Lightfall authentication.

Provides a modal dialog for users to authenticate via any registered
AuthProviderPlugin. Renders one button per provider from the registry.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from enum import Enum, auto
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.auth.session import AuthState, SessionManager
from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.theme import scaled_px
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lightfall.auth.providers.base import AuthProvider
    from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin


class LoginResult(Enum):
    """Result of the login dialog."""

    AUTHENTICATED = auto()  # User logged in successfully
    GUEST = auto()  # User chose to continue as guest
    CANCELLED = auto()  # User cancelled/closed the dialog


class LoginDialog(LFDialog):
    """Modal dialog for user authentication.

    Renders one button per provider registered in AuthProviderRegistry.
    Shared credential form is revealed only for providers that need it.

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
        self._login_cancelled = False  # Track if user manually cancelled

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
        # Outer layout with minimal margins for logo
        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(0)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Logo (full width, minimal margins)
        try:
            from lightfall.resources import get_logo_pixmap

            logo_pixmap = get_logo_pixmap()  # Load at full resolution
            if not logo_pixmap.isNull():
                logo_label = QLabel()
                logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                logo_label.setPixmap(
                    logo_pixmap.scaledToWidth(
                        500,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                outer_layout.addWidget(logo_label)
                logger.debug("Login dialog logo loaded successfully")
            else:
                logger.warning("Login dialog logo pixmap is null")
        except Exception as e:
            logger.warning("Failed to load login dialog logo: {}", e)

        # Content area with standard margins
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 12, 24, 24)
        outer_layout.addWidget(content_widget)

        # Title/header
        if self._show_on_expiry:
            header_text = "Session Expired"
            default_message = (
                "Your session has expired. Please log in again to continue "
                "or continue as a guest with limited access."
            )
        else:
            header_text = "Welcome to Lightfall"
            default_message = (
                "Please log in to access all features, or continue as a guest "
                "with limited access."
            )

        header = QLabel(header_text)
        header.setStyleSheet(f"font-size: {scaled_px(18)}px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Message
        self._message_label = QLabel(message or default_message)
        self._message_label.setWordWrap(True)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._message_label)

        layout.addSpacing(8)

        # Shared credential form (revealed only for providers that need it).
        self._cred_form = self._create_cred_form()
        self._cred_form.setVisible(False)
        layout.addWidget(self._cred_form)

        # Progress indicator (hidden by default) — unchanged from before.
        self._progress_widget = QWidget()
        progress_layout = QVBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(8)
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setTextVisible(False)
        progress_row.addWidget(self._progress_bar)
        self._progress_label = QLabel("Logging in...")
        progress_row.addWidget(self._progress_label)
        progress_layout.addLayout(progress_row)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setMinimumHeight(32)
        self._cancel_btn.clicked.connect(self._on_cancel_login)
        progress_layout.addWidget(self._cancel_btn)
        self._progress_widget.setVisible(False)
        layout.addWidget(self._progress_widget)

        # Error label (hidden by default).
        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: #cc0000; font-size: {scaled_px(12)}px;")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        # One button per registered provider.
        self._provider_buttons: list[QPushButton] = []
        self._pending_plugin: AuthProviderPlugin | None = None
        self._add_provider_buttons(layout)

        # Guest button.
        if self._allow_guest:
            self._guest_btn = QPushButton("Continue as Guest")
            self._guest_btn.setMinimumHeight(36)
            self._guest_btn.clicked.connect(self._on_guest_clicked)
            layout.addWidget(self._guest_btn)
        else:
            self._guest_btn = None

        self._info_label = QLabel("Guest access provides read-only permissions.")
        self._info_label.setStyleSheet(f"color: gray; font-size: {scaled_px(10)}px;")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

    def _create_cred_form(self) -> QWidget:
        """Username/password form, reused by any provider that needs input."""
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText("Username")
        self._username_edit.setMinimumHeight(32)
        layout.addWidget(self._username_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setPlaceholderText("Password")
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setMinimumHeight(32)
        self._password_edit.returnPressed.connect(self._on_form_submit)
        layout.addWidget(self._password_edit)

        self._cred_submit_btn = QPushButton("Login")
        self._cred_submit_btn.setMinimumHeight(40)
        self._cred_submit_btn.clicked.connect(self._on_form_submit)
        layout.addWidget(self._cred_submit_btn)
        return form

    def _add_provider_buttons(self, layout) -> None:
        from lightfall.auth.provider_registry import AuthProviderRegistry

        for plugin in AuthProviderRegistry.get_instance().get_all():
            btn = QPushButton(plugin.display_name)
            btn.setMinimumHeight(42)
            btn.clicked.connect(lambda _c=False, p=plugin: self._on_provider_login(p))
            layout.addWidget(btn)
            self._provider_buttons.append(btn)
            logger.debug("Login dialog: added provider button '{}'", plugin.name)

    def _on_provider_login(self, plugin: AuthProviderPlugin) -> None:
        """Provider button clicked: auth immediately, or reveal the form."""
        if not plugin.requires_username and not plugin.requires_password:
            self._start_provider_login(plugin, "", "")
            return
        # Needs credentials — reveal the form bound to this plugin.
        self._pending_plugin = plugin
        self._cred_form.setVisible(True)
        self._password_edit.setVisible(plugin.requires_password)
        self._error_label.setVisible(False)
        self._username_edit.setFocus()

    def _on_form_submit(self) -> None:
        plugin = self._pending_plugin
        if plugin is None:
            return
        username = self._username_edit.text().strip()
        password = self._password_edit.text() if plugin.requires_password else ""
        if plugin.requires_username and not username:
            self._show_error("Please enter a username")
            return
        if plugin.requires_password and not password:
            self._show_error("Please enter a password")
            return
        self._start_provider_login(plugin, username, password)

    def _start_provider_login(self, plugin: AuthProviderPlugin, username: str, password: str) -> None:
        self._login_cancelled = False
        for b in self._provider_buttons:
            b.setEnabled(False)
        self._cred_submit_btn.setEnabled(False)
        if self._guest_btn:
            self._guest_btn.setEnabled(False)
        self._progress_label.setText(f"Logging in via {plugin.display_name}...")
        self._progress_widget.setVisible(True)
        self._error_label.setVisible(False)
        self.login_started.emit()
        logger.info("Starting login via provider '{}'", plugin.name)

        self._login_thread = QThreadFuture(
            self._do_provider_login,
            plugin,
            username,
            password,
            callback_slot=self._on_login_complete,
            except_slot=self._on_login_error,
            name=f"{plugin.name}_login",
        )
        self._login_thread.start()

    def _do_provider_login(self, plugin: AuthProviderPlugin, username: str, password: str) -> bool:
        from datetime import datetime

        from lightfall.ui.preferences.login_settings import LoginSettingsProvider

        provider = plugin.create_provider()
        self._current_provider = provider
        session = asyncio.run(
            provider.authenticate(username=username or None, password=password or None)
        )
        if session:
            duration = LoginSettingsProvider.get_session_duration()
            session.user.expires_at = datetime.now(UTC) + duration
            self._session_manager.set_provider(provider)
            self._session_manager.attach_session(session)
            return True
        return False

    def _on_cancel_login(self) -> None:
        """Handle cancel button click during login."""
        logger.info("User cancelled login")
        self._login_cancelled = True

        # Cancel the background thread if running
        if self._login_thread and self._login_thread.isRunning():
            self._login_thread.cancel(timeout_ms=1000)

        # Reset UI immediately
        self._reset_ui()

    @property
    def login_result(self) -> LoginResult:
        """Get the login result after dialog closes."""
        return self._login_result

    def _on_login_complete(self, success: bool) -> None:
        """Handle login completion (called in main thread).

        Args:
            success: Whether login succeeded.
        """
        # Ignore callback if user already cancelled
        if self._login_cancelled:
            return

        if success:
            self._login_result = LoginResult.AUTHENTICATED
            self.accept()
        else:
            self._reset_ui()
            self._show_error("Login failed or was cancelled")

    def _on_login_error(self, error: Exception) -> None:
        """Handle login error (called in main thread).

        Args:
            error: The exception that occurred.
        """
        # Ignore callback if user already cancelled
        if self._login_cancelled:
            return

        logger.error("Login failed: {}", error)
        self._reset_ui()
        self._show_error(f"Login failed: {error}")

    def _reset_ui(self) -> None:
        """Reset UI after a failed/cancelled login attempt."""
        for b in self._provider_buttons:
            b.setEnabled(True)
        self._cred_submit_btn.setEnabled(True)
        self._username_edit.setEnabled(True)
        self._password_edit.setEnabled(True)
        self._password_edit.clear()
        if self._guest_btn:
            self._guest_btn.setEnabled(True)
        self._progress_widget.setVisible(False)
        self._cred_form.setVisible(False)
        self._pending_plugin = None

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
