"""Keycloak OIDC authentication provider.

This provider integrates with Keycloak for production authentication,
supporting browser-based OIDC flows and token refresh.
"""

from __future__ import annotations

import asyncio
import secrets
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

from lucid.auth.policy import Role
from lucid.auth.providers.base import AuthProvider
from lucid.auth.session import Session, User
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Callable


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
        """Get the proxy URL, auto-detecting for *.lbl.gov if not specified.

        Returns:
            The proxy URL to use, or None if no proxy needed.
        """
        if self.proxy_url is not None:
            return self.proxy_url if self.proxy_url else None

        # Auto-detect: use SOCKS proxy for *.lbl.gov URLs
        parsed = urlparse(self.server_url)
        if parsed.hostname and parsed.hostname.endswith(".lbl.gov"):
            return "socks5://localhost:1080"

        return None

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
    "ncs-scientist": Role.BEAMLINE_SCIENTIST,
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
            self._send_response("Authentication failed. You can close this window.")
        elif "code" in params:
            _OAuthCallbackHandler.callback_result = {
                "code": params["code"][0],
                "state": params.get("state", [None])[0],
            }
            self._send_response("Authentication successful! You can close this window.")
        else:
            self._send_response("Invalid callback. You can close this window.")

    def _send_response(self, message: str) -> None:
        """Send an HTML response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>NCS Authentication</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>{message}</h2>
        </body>
        </html>
        """
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
            except ImportError:
                raise ImportError(
                    "aiohttp is required for Keycloak authentication. "
                    "Install with: pip install lucid[keycloak]"
                )

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

    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Session | None:
        """
        Authenticate via browser-based OIDC flow.

        This opens a browser for the user to authenticate with Keycloak,
        then captures the callback and exchanges the code for tokens.

        Args:
            username: Ignored (browser auth).
            password: Ignored (browser auth).
            **kwargs: Additional parameters.

        Returns:
            Session if authentication succeeds, None otherwise.
        """
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
        logger.info("Opening browser for authentication: {}", auth_url)
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
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        else:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        user = User(
            username=username,
            display_name=display_name,
            email=email,
            roles=roles,
            groups=set(decoded.get("groups", [])),
            attributes=decoded,
            authenticated_at=datetime.now(timezone.utc),
            expires_at=expires_at,
        )

        session = Session(
            user=user,
            token=access_token,
            refresh_token=tokens.get("refresh_token"),
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
        """End session with Keycloak."""
        if not session.token:
            return

        http = await self._ensure_http()

        # Revoke the token
        data = {
            "client_id": self._config.client_id,
            "token": session.token,
        }
        if self._config.client_secret:
            data["client_secret"] = self._config.client_secret

        try:
            async with http.post(self._config.logout_url, data=data) as resp:
                if resp.status != 204:
                    logger.warning("Logout request returned status {}", resp.status)
        except Exception as e:
            logger.warning("Logout error: {}", e)

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
