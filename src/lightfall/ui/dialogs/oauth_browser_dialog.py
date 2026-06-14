"""OAuth browser dialog with embedded QWebEngineView.

Provides an embedded browser for OAuth flows that can auto-close
after authentication completes. Falls back gracefully if WebEngine
is not installed.

Supports optional SOCKS5 proxy configuration for accessing servers
behind firewalls.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.theme import scaled_px
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    pass

# Track whether WebEngine has been initialized (proxy must be set before first use)
_webengine_initialized = False


def _is_webengine_available() -> bool:
    """Check if QWebEngineView is available."""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        return True
    except ImportError:
        return False


WEBENGINE_AVAILABLE = _is_webengine_available()


class OAuthBrowserDialog(LFDialog):
    """Dialog with embedded browser for OAuth authentication.

    This dialog embeds a QWebEngineView to handle OAuth flows within
    the application, allowing automatic closure when authentication
    completes (instead of requiring the user to manually close a
    browser tab).

    The dialog intercepts navigation to the callback URL and extracts
    the authorization code or error from the URL parameters.

    Signals:
        auth_code_received: Emitted with (code, state) when auth succeeds.
        auth_error: Emitted with error message when auth fails.
        auth_cancelled: Emitted when user closes the dialog.

    Example:
        >>> dialog = OAuthBrowserDialog(auth_url, callback_url)
        >>> dialog.auth_code_received.connect(handle_code)
        >>> dialog.auth_error.connect(handle_error)
        >>> dialog.exec()
    """

    auth_code_received = Signal(str, str)  # code, state
    auth_error = Signal(str)  # error message
    auth_cancelled = Signal()

    def __init__(
        self,
        auth_url: str,
        callback_url: str = "http://localhost:8089/callback",
        parent: QWidget | None = None,
        title: str = "Login with Keycloak",
        proxy_url: str | None = None,
    ) -> None:
        """Initialize the OAuth browser dialog.

        Args:
            auth_url: The OAuth authorization URL to load.
            callback_url: The callback URL to intercept.
            parent: Parent widget.
            title: Dialog window title.
            proxy_url: Optional SOCKS5 proxy URL (e.g., "socks5://localhost:1080").
                       Must be set before the first QWebEngineView is created in
                       the application. If WebEngine was already initialized without
                       proxy, this parameter will be ignored with a warning.
        """
        super().__init__(parent)
        self._auth_url = auth_url
        self._callback_url = callback_url
        self._callback_host = urlparse(callback_url).netloc
        self._callback_path = urlparse(callback_url).path
        self._proxy_url = proxy_url
        self._completed = False

        self.setWindowTitle(title)
        self.setMinimumSize(800, 700)
        self.resize(900, 750)

        # Configure proxy before setting up UI (which creates WebEngineView)
        if proxy_url:
            self._configure_proxy(proxy_url)

        self._setup_ui()

    def _configure_proxy(self, proxy_url: str) -> None:
        """Configure SOCKS5 proxy for WebEngine via Chromium flags.

        WebEngine uses Chromium's network stack, which reads proxy settings
        from the QTWEBENGINE_CHROMIUM_FLAGS environment variable. This must
        be set before the first QWebEngineView is created.

        Args:
            proxy_url: Proxy URL (e.g., "socks5://localhost:1080").
        """
        global _webengine_initialized

        if _webengine_initialized:
            logger.warning(
                "WebEngine already initialized - proxy setting may not take effect. "
                "For proxy support, ensure OAuthBrowserDialog with proxy is created "
                "before any other WebEngine usage in the application."
            )
            return

        # Parse proxy URL to build Chromium flag
        parsed = urlparse(proxy_url)
        if parsed.scheme not in ("socks5", "socks4", "http", "https"):
            logger.warning("Unsupported proxy scheme: {}. Using as-is.", parsed.scheme)

        # Set Chromium flag for proxy
        # Format: --proxy-server=socks5://host:port
        existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        proxy_flag = f"--proxy-server={proxy_url}"

        if proxy_flag not in existing_flags:
            new_flags = f"{existing_flags} {proxy_flag}".strip()
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = new_flags
            logger.debug("Configured WebEngine proxy: {}", proxy_url)

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if WEBENGINE_AVAILABLE:
            self._setup_webengine_ui(layout)
        else:
            self._setup_fallback_ui(layout)

    def _setup_webengine_ui(self, layout: QVBoxLayout) -> None:
        """Set up UI with embedded QWebEngineView."""
        global _webengine_initialized
        from PySide6.QtWebEngineWidgets import QWebEngineView

        # Mark as initialized (proxy must be set before this point)
        _webengine_initialized = True

        # Progress bar at top
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(3)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: none;
                background: transparent;
            }
            QProgressBar::chunk {
                background-color: #0066cc;
            }
            """
        )
        layout.addWidget(self._progress_bar)

        # Web view
        self._web_view = QWebEngineView()
        self._web_view.setUrl(QUrl(self._auth_url))

        # Connect signals
        self._web_view.loadProgress.connect(self._on_load_progress)
        self._web_view.loadFinished.connect(self._on_load_finished)
        self._web_view.urlChanged.connect(self._on_url_changed)

        layout.addWidget(self._web_view)

        logger.debug("OAuth dialog: loading auth URL in embedded browser")

    def _setup_fallback_ui(self, layout: QVBoxLayout) -> None:
        """Set up fallback UI when WebEngine is not available."""
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Warning icon/message
        warning_label = QLabel(
            "Embedded browser not available.\n\n"
            "QtWebEngine is not available in your PySide6 installation.\n"
            "Please close this dialog and use the external browser flow."
        )
        warning_label.setStyleSheet(f"font-size: {scaled_px(14)}px;")
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setMinimumHeight(36)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        layout.addStretch()

        logger.warning(
            "OAuth dialog: WebEngine not available, showing fallback UI. "
            "Install PySide6-WebEngine for embedded browser support."
        )

    def _on_load_progress(self, progress: int) -> None:
        """Handle page load progress.

        Args:
            progress: Load progress percentage (0-100).
        """
        self._progress_bar.setValue(progress)

    def _on_load_finished(self, ok: bool) -> None:
        """Handle page load completion.

        Args:
            ok: Whether the load was successful.
        """
        if ok:
            self._progress_bar.setValue(100)
        else:
            # Don't show error if we already completed (intercepted callback)
            if not self._completed:
                logger.warning("OAuth dialog: page load failed")

    def _on_url_changed(self, url: QUrl) -> None:
        """Handle URL changes to intercept the callback.

        Args:
            url: The new URL being navigated to.
        """
        url_str = url.toString()
        parsed = urlparse(url_str)

        # Check if this is the callback URL
        if parsed.netloc == self._callback_host and parsed.path == self._callback_path:
            logger.debug("OAuth dialog: intercepted callback URL")
            self._completed = True

            # Parse query parameters
            params = parse_qs(parsed.query)

            if "error" in params:
                error = params["error"][0]
                error_desc = params.get("error_description", ["Unknown error"])[0]
                logger.warning("OAuth error: {} - {}", error, error_desc)
                self.auth_error.emit(f"{error}: {error_desc}")
                self.reject()

            elif "code" in params:
                code = params["code"][0]
                state = params.get("state", [None])[0]
                logger.debug("OAuth dialog: received authorization code")
                self.auth_code_received.emit(code, state or "")
                self.accept()

            else:
                logger.warning("OAuth dialog: callback missing code parameter")
                self.auth_error.emit("Invalid callback - missing authorization code")
                self.reject()

    def closeEvent(self, event) -> None:
        """Handle dialog close event."""
        if not self._completed:
            logger.debug("OAuth dialog: user cancelled")
            self.auth_cancelled.emit()
        super().closeEvent(event)

    def reject(self) -> None:
        """Handle dialog rejection (Escape key or close)."""
        if not self._completed:
            self.auth_cancelled.emit()
        super().reject()

    @classmethod
    def is_available(cls) -> bool:
        """Check if the embedded browser dialog is available.

        Returns:
            True if WebEngine is installed and available.
        """
        return WEBENGINE_AVAILABLE
