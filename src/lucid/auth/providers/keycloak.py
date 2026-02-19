"""Keycloak OIDC authentication provider.

This provider integrates with Keycloak for production authentication,
supporting browser-based OIDC flows and token refresh.

The provider supports two browser modes:
1. Embedded browser (QWebEngineView) - auto-closes after auth, better UX
2. External browser - fallback when WebEngine not available
"""

from __future__ import annotations

import secrets
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

from lucid.auth.policy import Role
from lucid.auth.providers.base import AuthProvider
from lucid.auth.session import Session, User
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


@dataclass
class KeycloakConfig:
    """Keycloak connection configuration.

    Attributes:
        server_url: Base URL of the Keycloak server.
        realm: Keycloak realm name.
        client_id: OIDC client ID.
        client_secret: OIDC client secret (optional for public clients).
        redirect_uri: OAuth callback URI.
        scope: OIDC scopes to request.
        proxy_url: SOCKS proxy URL (e.g., socks5://localhost:1080).
                   If None, auto-detects for *.lbl.gov URLs.
    """

    server_url: str
    realm: str
    client_id: str
    client_secret: str | None = None
    redirect_uri: str = "http://localhost:8089/callback"
    scope: str = "openid profile email"
    proxy_url: str | None = None

    def get_proxy_url(self) -> str | None:
        """Get the proxy URL from settings or explicit configuration.

        If proxy_url was explicitly set in the constructor, use that.
        Otherwise, delegate to ProxySettingsProvider which respects
        user settings (disabled by default).

        Returns:
            The proxy URL to use, or None if no proxy needed.
        """
        if self.proxy_url is not None:
            return self.proxy_url if self.proxy_url else None

        # Use centralized proxy settings
        from lucid.ui.preferences.proxy_settings import ProxySettingsProvider

        return ProxySettingsProvider.should_use_proxy_for_url(self.server_url)

    @property
    def auth_url(self) -> str:
        """Authorization endpoint URL."""
        return f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/auth"

    @property
    def token_url(self) -> str:
        """Token endpoint URL."""
        return f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/token"

    @property
    def userinfo_url(self) -> str:
        """User info endpoint URL."""
        return f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/userinfo"

    @property
    def logout_url(self) -> str:
        """Logout endpoint URL."""
        return f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/logout"

    @property
    def introspect_url(self) -> str:
        """Token introspection endpoint URL."""
        return f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/token/introspect"


# Role mapping from Keycloak groups/roles to NCS roles
DEFAULT_ROLE_MAPPING: dict[str, Role] = {
    # Keycloak role/group name -> NCS Role
    "ncs-admin": Role.ADMIN,
    "ncs-developer": Role.DEVELOPER,
    "ncs-staff": Role.STAFF,
    "ncs-scientist": Role.STAFF,  # Scientist role consolidated into staff
    "ncs-operator": Role.OPERATOR,
    "ncs-user": Role.USER,
    "als-staff": Role.STAFF,
    "als-user": Role.USER,
}


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to receive OAuth callback."""

    callback_result: dict[str, Any] | None = None
    error: str | None = None

    def do_GET(self) -> None:
        """Handle the OAuth callback GET request."""
        parsed = urlparse(self.path)

        if parsed.path != "/callback":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)

        if "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            error_desc = params.get("error_description", ["Authentication was denied"])[0]
            self._send_response(
                success=False,
                title="Authentication Failed",
                message=error_desc,
            )
        elif "code" in params:
            _OAuthCallbackHandler.callback_result = {
                "code": params["code"][0],
                "state": params.get("state", [None])[0],
            }
            self._send_response(
                success=True,
                title="Authentication Successful",
                message="You have been logged in successfully.",
            )
        else:
            self._send_response(
                success=False,
                title="Invalid Callback",
                message="The authentication response was invalid.",
            )

    def _send_response(self, success: bool, title: str, message: str) -> None:
        """Send an HTML response with improved styling and auto-close attempt.

        Args:
            success: Whether authentication succeeded.
            title: The title to display.
            message: The message to display.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        # Color scheme based on success/failure
        if success:
            icon = "&#10004;"  # Checkmark
            icon_color = "#22c55e"  # Green
            border_color = "#22c55e"
        else:
            icon = "&#10006;"  # X mark
            icon_color = "#ef4444"  # Red
            border_color = "#ef4444"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>LUCID - {title}</title>
    <meta charset="utf-8">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1e3a5f 0%, #0d1b2a 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 12px;
            padding: 48px 40px;
            max-width: 420px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
            border-top: 4px solid {border_color};
        }}
        .icon {{
            font-size: 64px;
            color: {icon_color};
            margin-bottom: 24px;
        }}
        h1 {{
            color: #1e293b;
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 12px;
        }}
        .message {{
            color: #64748b;
            font-size: 16px;
            line-height: 1.5;
            margin-bottom: 24px;
        }}
        .hint {{
            color: #94a3b8;
            font-size: 14px;
            padding-top: 16px;
            border-top: 1px solid #e2e8f0;
        }}
        .hint.hidden {{
            display: none;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <p class="message">{message}</p>
        <p class="hint" id="close-hint">You can close this tab and return to LUCID.</p>
    </div>
    <script>
        // Try to close the window after a brief delay
        setTimeout(function() {{
            try {{
                window.close();
            }} catch (e) {{
                // Ignore - some browsers block this
            }}
            // If we're still here after attempting close, show the hint
            setTimeout(function() {{
                document.getElementById('close-hint').classList.remove('hidden');
            }}, 500);
        }}, 1500);
    </script>
</body>
</html>"""
        self.wfile.write(html.encode())

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP logging."""
        pass


class KeycloakAuthProvider(AuthProvider):
    """
    Keycloak OIDC authentication provider.

    This provider implements the OAuth 2.0 Authorization Code flow
    for authenticating users via Keycloak. It opens a browser for
    the user to log in and captures the callback.

    Role Mapping:
        Keycloak groups/roles are mapped to NCS roles via role_mapping.
        Groups named 'ncs-admin', 'ncs-user', etc. map to corresponding
        NCS roles. Custom mappings can be provided.

    Example:
        >>> config = KeycloakConfig(
        ...     server_url="https://keycloak.example.com",
        ...     realm="als",
        ...     client_id="ncs-app",
        ... )
        >>> provider = KeycloakAuthProvider(config)
        >>> session = await provider.authenticate()  # Opens browser
    """

    def __init__(
        self,
        config: KeycloakConfig,
        role_mapping: dict[str, Role] | None = None,
        callback_timeout: int = 120,
    ) -> None:
        """
        Initialize the Keycloak provider.

        Args:
            config: Keycloak connection configuration.
            role_mapping: Custom Keycloak role/group to NCS role mapping.
            callback_timeout: Seconds to wait for OAuth callback.
        """
        self._config = config
        self._role_mapping = role_mapping or DEFAULT_ROLE_MAPPING
        self._callback_timeout = callback_timeout
        self._http: Any = None  # aiohttp ClientSession

    @property
    def name(self) -> str:
        return f"Keycloak ({self._config.realm})"

    @property
    def supports_password_auth(self) -> bool:
        # Could support resource owner password grant, but browser flow preferred
        return False

    @property
    def supports_browser_auth(self) -> bool:
        return True

    async def _ensure_http(self) -> Any:
        """Ensure aiohttp client session exists.

        Creates an aiohttp ClientSession with SOCKS proxy support if configured
        or auto-detected for *.lbl.gov URLs.
        """
        if self._http is None:
            try:
                import aiohttp
            except ImportError as err:
                raise ImportError(
                    "aiohttp is required for Keycloak authentication. "
                    "Install with: pip install lucid[keycloak]"
                ) from err

            connector = None
            proxy_url = self._config.get_proxy_url()

            if proxy_url:
                try:
                    from aiohttp_socks import ProxyConnector

                    connector = ProxyConnector.from_url(proxy_url)
                    logger.debug("Using SOCKS proxy: {}", proxy_url)
                except ImportError:
                    logger.warning(
                        "aiohttp-socks not installed, proxy {} will not be used. "
                        "Install with: pip install lucid[keycloak]",
                        proxy_url,
                    )

            self._http = aiohttp.ClientSession(connector=connector)
        return self._http

    @staticmethod
    def _is_remote_display() -> bool:
        """Detect if running on a remote/virtual display (VNC, X11 forwarding).

        Returns:
            True if a remote display environment is detected.
        """
        import os

        # Check for VNC-specific environment variables
        if os.environ.get("VNCDESKTOP") or os.environ.get("VNC_SESSION"):
            return True

        # Check DISPLAY for remote X11 or high display numbers (VNC)
        display = os.environ.get("DISPLAY", "")
        if display:
            # Remote X11: hostname:0
            if ":" in display and not display.startswith(":"):
                return True
            # High display numbers often indicate VNC (:1, :2, etc.)
            try:
                display_num = int(display.split(":")[1].split(".")[0])
                if display_num > 0:
                    return True
            except (IndexError, ValueError):
                pass

        return False

    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        *,
        use_embedded_browser: bool = True,
        parent_widget: QWidget | None = None,
        **kwargs: Any,
    ) -> Session | None:
        """
        Authenticate via browser-based OIDC flow.

        This opens a browser for the user to authenticate with Keycloak,
        then captures the callback and exchanges the code for tokens.

        By default, tries to use an embedded QWebEngineView browser which
        can auto-close after authentication. Falls back to external browser
        if WebEngine is not available or when running over VNC/remote display.

        Args:
            username: Ignored (browser auth).
            password: Ignored (browser auth).
            use_embedded_browser: Whether to try embedded browser first.
            parent_widget: Parent widget for the embedded browser dialog.
            **kwargs: Additional parameters.

        Returns:
            Session if authentication succeeds, None otherwise.
        """
        # Skip embedded browser for remote displays (VNC, X11 forwarding)
        # WebEngine crashes/freezes over VNC even with software rendering
        if use_embedded_browser and self._is_remote_display():
            logger.info("Remote display detected - using external browser for authentication")
            use_embedded_browser = False

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": self._config.scope,
            "state": state,
        }
        auth_url = f"{self._config.auth_url}?{urlencode(params)}"

        # Try embedded browser first if requested and available
        if use_embedded_browser:
            result = self._auth_with_embedded_browser(auth_url, state, parent_widget)
            if result is not None:
                # result is either a code or False (cancelled/error)
                if result is False:
                    return None
                # Got an auth code, exchange it
                return await self._exchange_code(result)

        # Fall back to external browser with callback server
        return await self._auth_with_external_browser(auth_url, state)

    def _auth_with_embedded_browser(
        self,
        auth_url: str,
        state: str,
        parent_widget: QWidget | None = None,
    ) -> str | bool | None:
        """Attempt authentication using embedded QWebEngineView browser.

        This method handles both main thread and background thread calls.
        When called from a background thread, it uses invoke_in_main_thread
        to show the dialog on the main thread and waits for the result.

        Args:
            auth_url: The OAuth authorization URL.
            state: The CSRF state token.
            parent_widget: Parent widget for the dialog.

        Returns:
            - Authorization code string if successful
            - False if cancelled or error
            - None if embedded browser not available (should fall back)
        """
        try:
            from lucid.ui.dialogs.oauth_browser_dialog import OAuthBrowserDialog
        except ImportError:
            logger.debug("OAuthBrowserDialog not available, using external browser")
            return None

        if not OAuthBrowserDialog.is_available():
            logger.debug("WebEngine not available, using external browser")
            return None

        # Check if we're in a Qt application context
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is None:
                logger.debug("No Qt application, using external browser")
                return None
        except ImportError:
            return None

        # Import threading utilities
        from lucid.utils.threads import invoke_in_main_thread, is_main_thread

        logger.info("Using embedded browser for authentication")

        # Store result from Qt signals
        result_holder: dict[str, Any] = {"code": None, "state": None, "error": None}
        completed = Event()

        def on_code_received(code: str, recv_state: str) -> None:
            result_holder["code"] = code
            result_holder["state"] = recv_state
            completed.set()

        def on_error(error: str) -> None:
            result_holder["error"] = error
            completed.set()

        def on_cancelled() -> None:
            result_holder["error"] = "cancelled"
            completed.set()

        def show_dialog() -> None:
            """Create and show the OAuth dialog on the main thread."""
            dialog = OAuthBrowserDialog(
                auth_url=auth_url,
                callback_url=self._config.redirect_uri,
                parent=parent_widget,
                title=f"Login - {self._config.realm}",
                proxy_url=self._config.get_proxy_url(),
            )
            dialog.auth_code_received.connect(on_code_received)
            dialog.auth_error.connect(on_error)
            dialog.auth_cancelled.connect(on_cancelled)

            # Execute dialog (blocks until closed)
            dialog.exec()

        if is_main_thread():
            # Already on main thread, show directly
            show_dialog()
        else:
            # On background thread, invoke on main thread and wait
            invoke_in_main_thread(show_dialog)
            # Wait for the dialog to complete (with timeout)
            completed.wait(timeout=self._callback_timeout)

        # Check result
        if result_holder["error"]:
            if result_holder["error"] == "cancelled":
                logger.info("User cancelled embedded browser login")
            else:
                logger.error("Embedded browser auth error: {}", result_holder["error"])
            return False

        if not result_holder["code"]:
            logger.error("No authorization code received from embedded browser")
            return False

        # Verify state
        if result_holder["state"] != state:
            logger.error("State mismatch - possible CSRF attack")
            return False

        return result_holder["code"]

    async def _auth_with_external_browser(
        self,
        auth_url: str,
        state: str,
    ) -> Session | None:
        """Authenticate using external browser with callback server.

        Args:
            auth_url: The OAuth authorization URL.
            state: The CSRF state token.

        Returns:
            Session if successful, None otherwise.
        """
        # Reset callback handler state
        _OAuthCallbackHandler.callback_result = None
        _OAuthCallbackHandler.error = None

        # Start callback server
        parsed_redirect = urlparse(self._config.redirect_uri)
        port = parsed_redirect.port or 8089

        server = HTTPServer(("localhost", port), _OAuthCallbackHandler)
        server.timeout = self._callback_timeout

        def run_server() -> None:
            server.handle_request()

        server_thread = Thread(target=run_server, daemon=True)
        server_thread.start()

        # Open browser
        logger.info("Opening external browser for authentication")
        webbrowser.open(auth_url)

        # Wait for callback
        server_thread.join(timeout=self._callback_timeout)
        server.server_close()

        if _OAuthCallbackHandler.error:
            logger.error("Authentication error: {}", _OAuthCallbackHandler.error)
            return None

        if not _OAuthCallbackHandler.callback_result:
            logger.error("Authentication timed out")
            return None

        result = _OAuthCallbackHandler.callback_result

        # Verify state
        if result.get("state") != state:
            logger.error("State mismatch - possible CSRF attack")
            return None

        # Exchange code for tokens
        return await self._exchange_code(result["code"])

    async def _exchange_code(self, code: str) -> Session | None:
        """Exchange authorization code for tokens."""
        http = await self._ensure_http()

        data = {
            "grant_type": "authorization_code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "code": code,
        }

        if self._config.client_secret:
            data["client_secret"] = self._config.client_secret

        try:
            async with http.post(self._config.token_url, data=data) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error("Token exchange failed: {}", error)
                    return None

                tokens = await resp.json()

            return await self._create_session_from_tokens(tokens)

        except Exception as e:
            logger.error("Token exchange error: {}", e)
            return None

    async def _create_session_from_tokens(
        self, tokens: dict[str, Any]
    ) -> Session | None:
        """Create a session from token response."""
        access_token = tokens.get("access_token")
        if not access_token:
            return None

        # Decode token to get user info (basic decode, not verification)
        try:
            import base64
            import json

            # Split token and decode payload
            parts = access_token.split(".")
            if len(parts) != 3:
                logger.error("Invalid token format")
                return None

            # Add padding if needed
            payload = parts[1]
            payload += "=" * (4 - len(payload) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload))

        except Exception as e:
            logger.error("Failed to decode token: {}", e)
            return None

        # Extract user info
        username = decoded.get("preferred_username", decoded.get("sub", "unknown"))
        email = decoded.get("email", "")
        display_name = decoded.get("name", username)

        # Extract roles from token claims
        roles = self._extract_roles(decoded)

        # Calculate expiry
        exp = decoded.get("exp")
        if exp:
            expires_at = datetime.fromtimestamp(exp, tz=UTC)
        else:
            expires_at = datetime.now(UTC) + timedelta(hours=1)

        user = User(
            username=username,
            display_name=display_name,
            email=email,
            roles=roles,
            groups=set(decoded.get("groups", [])),
            attributes=decoded,
            authenticated_at=datetime.now(UTC),
            expires_at=expires_at,
        )

        session = Session(
            user=user,
            token=access_token,
            refresh_token=tokens.get("refresh_token"),
            id_token=tokens.get("id_token"),
        )

        logger.info("User '{}' authenticated via Keycloak", username)
        return session

    def _extract_roles(self, token_claims: dict[str, Any]) -> set[Role]:
        """Extract NCS roles from token claims."""
        roles: set[Role] = set()

        # Check realm_access.roles
        realm_roles = token_claims.get("realm_access", {}).get("roles", [])

        # Check resource_access.{client_id}.roles
        resource_roles = (
            token_claims.get("resource_access", {})
            .get(self._config.client_id, {})
            .get("roles", [])
        )

        # Check groups
        groups = token_claims.get("groups", [])

        # Map all to NCS roles
        for claim in realm_roles + resource_roles + groups:
            claim_lower = claim.lower()
            if claim_lower in self._role_mapping:
                roles.add(self._role_mapping[claim_lower])
            # Also check without prefix
            for prefix in ["ncs-", "als-"]:
                if claim_lower.startswith(prefix):
                    base = claim_lower[len(prefix) :]
                    try:
                        roles.add(Role(base))
                    except ValueError:
                        pass

        # Default to USER if authenticated but no specific role
        if not roles:
            roles = {Role.USER}

        return roles

    async def logout(self, session: Session) -> None:
        """End session with Keycloak.

        Performs a full logout:
        1. Revokes the token via Keycloak's logout endpoint (ends SSO session)
        2. Clears embedded browser cookies (prevents auto-login on next auth)
        """
        if not session.token:
            return

        http = await self._ensure_http()

        # Use RP-initiated logout with id_token_hint to end the SSO session
        data: dict[str, str] = {
            "client_id": self._config.client_id,
        }
        if session.id_token:
            data["id_token_hint"] = session.id_token
        if session.refresh_token:
            data["refresh_token"] = session.refresh_token
        if self._config.client_secret:
            data["client_secret"] = self._config.client_secret

        try:
            async with http.post(self._config.logout_url, data=data) as resp:
                if resp.status not in (200, 204):
                    logger.warning("Logout request returned status {}", resp.status)
        except Exception as e:
            logger.warning("Logout error: {}", e)

        # Clear embedded browser cookies so Keycloak won't auto-login next time
        self._clear_browser_cookies()

    @staticmethod
    def _clear_browser_cookies() -> None:
        """Clear QWebEngine cookies to prevent Keycloak SSO auto-login.

        This removes the Keycloak session cookies from the embedded browser
        so the next login will prompt for credentials instead of silently
        re-authenticating with the cached session.
        """
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
            from PySide6.QtWebEngineCore import QWebEngineProfile

            profile = QWebEngineProfile.defaultProfile()
            if profile:
                cookie_store = profile.cookieStore()
                cookie_store.deleteAllCookies()
                logger.debug("Cleared embedded browser cookies")
        except ImportError:
            # WebEngine not available — nothing to clear
            pass
        except Exception as e:
            logger.warning("Failed to clear browser cookies: {}", e)

    async def refresh(self, session: Session) -> Session | None:
        """Refresh the session tokens."""
        if not session.refresh_token:
            return None

        http = await self._ensure_http()

        data = {
            "grant_type": "refresh_token",
            "client_id": self._config.client_id,
            "refresh_token": session.refresh_token,
        }
        if self._config.client_secret:
            data["client_secret"] = self._config.client_secret

        try:
            async with http.post(self._config.token_url, data=data) as resp:
                if resp.status != 200:
                    logger.warning("Token refresh failed")
                    return None

                tokens = await resp.json()

            new_session = await self._create_session_from_tokens(tokens)
            if new_session:
                # Preserve original creation time
                new_session.created_at = session.created_at
            return new_session

        except Exception as e:
            logger.error("Token refresh error: {}", e)
            return None

    async def check_connectivity(self) -> bool:
        """Check if Keycloak server is reachable."""
        http = await self._ensure_http()

        try:
            # Try to fetch the well-known configuration
            well_known_url = (
                f"{self._config.server_url}/realms/{self._config.realm}"
                "/.well-known/openid-configuration"
            )
            async with http.get(well_known_url, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def get_user_info(self, session: Session) -> dict[str, Any] | None:
        """Get user info from Keycloak userinfo endpoint."""
        if not session.token:
            return None

        http = await self._ensure_http()

        try:
            headers = {"Authorization": f"Bearer {session.token}"}
            async with http.get(self._config.userinfo_url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception as e:
            logger.error("Failed to get user info: {}", e)
            return None

    async def validate_token(self, token: str) -> bool:
        """Validate a token via introspection endpoint."""
        http = await self._ensure_http()

        data = {
            "client_id": self._config.client_id,
            "token": token,
        }
        if self._config.client_secret:
            data["client_secret"] = self._config.client_secret

        try:
            async with http.post(self._config.introspect_url, data=data) as resp:
                if resp.status != 200:
                    return False
                result = await resp.json()
                return result.get("active", False)
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._http:
            await self._http.close()
            self._http = None
