"""Network proxy settings plugin for NCS.

This module contains the ProxySettingsPlugin that allows users to
configure SOCKS/HTTP proxy settings for network connections.

The proxy is DISABLED by default - users must explicitly enable it.
Previously, proxy was auto-detected for *.lbl.gov URLs.
"""

from __future__ import annotations

import fnmatch
import os
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.setup.facilities import as_domain_list
from lightfall.ui.preferences.form.schema import (
    Action,
    Choice,
    FormValues,
    Info,
    Integer,
    Section,
    SettingField,
    Text,
    Toggle,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon


# Proxy type options
PROXY_TYPES = {
    "socks5": "SOCKS5",
    "socks4": "SOCKS4",
    "http": "HTTP",
}

# Default proxy configuration
DEFAULT_PROXY_HOST = "localhost"
DEFAULT_PROXY_PORT = 1080

# Auto-detect proxies these hosts; a facility bootstrap replaces the list
# (preference proxy_auto_detect_domains).
DEFAULT_PROXY_DOMAINS: tuple[str, ...] = ("*.lbl.gov",)


# Every key on this page is console-only, so everything is shown at "This console".
CONSOLE = frozenset({"local"})

INFO_TEXT = (
    "When to use a proxy: if you need to access LBNL internal services (*.lbl.gov) "
    "from outside the network, you can use an SSH tunnel as a SOCKS5 proxy "
    "(ssh -D 1080 -N username@remote.lbl.gov creates one on localhost:1080). "
    "The proxy setting affects Keycloak authentication, ALS beam status, and embedded "
    "browser (WebEngine) connections; WebEngine reads it only at startup, so changes "
    "need a restart."
)


def _connection_enabled(values: FormValues) -> bool:
    """Connection fields and Test Connection apply when the proxy or auto-detect is on."""
    return (
        values.effective("proxy_enabled", False) is True
        or values.effective("proxy_auto_detect", False) is True
    )


def _test_connection(values: FormValues) -> str:
    """TCP connect to the proxy (worker thread); raise to show the failure."""
    host = (values.effective("proxy_host", DEFAULT_PROXY_HOST) or "").strip() or DEFAULT_PROXY_HOST
    port = values.effective("proxy_port", DEFAULT_PROXY_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(5.0)
        result = sock.connect_ex((host, port))
    except TimeoutError:
        raise RuntimeError("Connection timeout") from None
    except socket.gaierror as e:
        raise RuntimeError(f"DNS error: {e}") from e
    except Exception as e:
        logger.error("Proxy test connection error: {}", e)
        raise RuntimeError(f"Error: {e}") from e
    finally:
        sock.close()
    if result != 0:
        raise RuntimeError(f"Connection refused (error {result})")
    return "Connected!"


class ProxySettingsProvider:
    """Provides proxy configuration from settings.

    This helper class provides a simple interface for components to
    retrieve proxy configuration. Used by KeycloakConfig, ALSBeamStatusService,
    and other network clients.

    The proxy is disabled by default. Users must explicitly enable it
    in Settings > Network Proxy.
    """

    @staticmethod
    def is_enabled() -> bool:
        """Check if proxy is enabled in settings.

        Returns:
            True if proxy is enabled.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_enabled", False)

    @staticmethod
    def get_proxy_type() -> str:
        """Get the configured proxy type.

        Returns:
            Proxy type string (socks5, socks4, or http).
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_type", "socks5")

    @staticmethod
    def get_proxy_host() -> str:
        """Get the configured proxy host.

        Returns:
            Proxy host string.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_host", DEFAULT_PROXY_HOST)

    @staticmethod
    def get_proxy_port() -> int:
        """Get the configured proxy port.

        Returns:
            Proxy port number.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_port", DEFAULT_PROXY_PORT)

    @staticmethod
    def is_auto_detect_enabled() -> bool:
        """Check if auto-detect for *.lbl.gov is enabled.

        Returns:
            True if auto-detect is enabled.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_auto_detect", False)

    @staticmethod
    def get_auto_detect_domains() -> list[str]:
        """Host patterns auto-detect sends through the proxy (fnmatch, lower case)."""
        prefs = PreferencesManager.get_instance()
        domains = as_domain_list(prefs.get("proxy_auto_detect_domains", None))
        return domains or list(DEFAULT_PROXY_DOMAINS)

    @staticmethod
    def host_matches(hostname: str, patterns: list[str]) -> bool:
        host = hostname.lower().rstrip(".")
        return any(fnmatch.fnmatchcase(host, pattern) for pattern in patterns)

    @staticmethod
    def get_proxy_url() -> str | None:
        """Get the full proxy URL if proxy is enabled.

        Returns:
            Proxy URL (e.g., socks5://localhost:1080) or None if disabled.
        """
        if not ProxySettingsProvider.is_enabled():
            return None

        proxy_type = ProxySettingsProvider.get_proxy_type()
        host = ProxySettingsProvider.get_proxy_host()
        port = ProxySettingsProvider.get_proxy_port()

        return f"{proxy_type}://{host}:{port}"

    @staticmethod
    def should_use_proxy_for_url(url: str) -> str | None:
        """Determine if proxy should be used for a given URL.

        This method checks:
        1. If proxy is globally enabled, return proxy URL
        2. If auto-detect is enabled and URL is *.lbl.gov, return proxy URL
        3. Otherwise return None

        Args:
            url: The URL to check.

        Returns:
            Proxy URL to use, or None if no proxy needed.
        """
        # If globally enabled, use proxy for everything
        if ProxySettingsProvider.is_enabled():
            return ProxySettingsProvider.get_proxy_url()

        # Auto-detect: proxy only the facility's domains (default *.lbl.gov)
        if ProxySettingsProvider.is_auto_detect_enabled():
            try:
                parsed = urlparse(url)
                if parsed.hostname and ProxySettingsProvider.host_matches(
                    parsed.hostname, ProxySettingsProvider.get_auto_detect_domains()
                ):
                    proxy_type = ProxySettingsProvider.get_proxy_type()
                    host = ProxySettingsProvider.get_proxy_host()
                    port = ProxySettingsProvider.get_proxy_port()
                    return f"{proxy_type}://{host}:{port}"
            except Exception:
                pass

        return None

    @staticmethod
    def _is_remote_display() -> bool:
        """Detect if running on a remote/virtual display (VNC, X11 forwarding).

        Returns:
            True if a remote display environment is detected.
        """
        # Check for VNC-specific environment variables
        if os.environ.get("VNCDESKTOP"):
            return True
        if os.environ.get("VNC_SESSION"):
            return True

        # Check DISPLAY - remote X11 or high display numbers suggest VNC
        display = os.environ.get("DISPLAY", "")
        if display:
            # Remote X11: hostname:0
            if ":" in display and not display.startswith(":"):
                return True
            # High display numbers often indicate VNC (e.g., :1, :2, etc.)
            # :0 is typically the local physical display
            try:
                display_num = int(display.split(":")[1].split(".")[0])
                if display_num > 0:
                    return True
            except (IndexError, ValueError):
                pass

        return False

    @staticmethod
    def configure_webengine() -> None:
        """Configure WebEngine for proxy and remote display compatibility.

        This MUST be called before any QWebEngineView is created, as
        WebEngine reads QTWEBENGINE_CHROMIUM_FLAGS only once at startup.

        Typically called from ProxySettingsPlugin.on_loaded() which runs
        during plugin preload, before the main window is created.

        Configures:
        - Software rendering for remote displays (VNC, X11 forwarding)
        - Proxy settings if enabled
        """
        existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        flags: list[str] = []

        # Remote display / VNC workarounds (env vars already set at module load)
        if ProxySettingsProvider._is_remote_display():
            logger.info(
                "Remote display detected (DISPLAY={}) - software rendering enabled",
                os.environ.get("DISPLAY", ""),
            )

        # Proxy configuration
        proxy_url = ProxySettingsProvider.get_proxy_url()
        if proxy_url:
            flags.append(f"--proxy-server={proxy_url}")
            logger.debug("Configured WebEngine proxy: {}", proxy_url)

        # Merge with existing flags
        for flag in flags:
            if flag not in existing:
                existing = f"{existing} {flag}".strip()

        if existing:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = existing

    # Keep old name as alias for backwards compatibility
    configure_webengine_proxy = configure_webengine


class ProxySettingsPlugin(SettingsPlugin):
    """Settings plugin for network proxy configuration.

    Allows users to configure:
    - Enable/disable proxy (master toggle, default: disabled)
    - Proxy type (socks5, socks4, http)
    - Proxy host and port
    - Auto-detect for *.lbl.gov URLs
    - Test connection functionality

    This plugin has preload=True to configure WebEngine proxy before
    any QWebEngineView is created.
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "proxy"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Network Proxy"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "network"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 25  # After appearance (10), before login (30)

    def on_loaded(self) -> None:
        """Called when plugin is loaded.

        Configure WebEngine early, before any WebEngine views are created.
        This is critical because QTWEBENGINE_CHROMIUM_FLAGS is only read once.
        """
        ProxySettingsProvider.configure_webengine()
        logger.debug("ProxySettingsPlugin loaded, WebEngine configured")

    def sections(self) -> Sequence[Section]:
        return [
            Section(
                "Proxy Configuration",
                [
                    SettingField(
                        "proxy_enabled",
                        Toggle(),
                        label="Enable proxy for all connections",
                        description=(
                            "When enabled, all network connections will use the configured "
                            "proxy. This is disabled by default."
                        ),
                        restart_required=True,
                    ),
                    SettingField(
                        "proxy_type",
                        Choice(list(PROXY_TYPES.items())),
                        label="Proxy Type",
                        description="Protocol type for the proxy connection",
                        enabled_when=_connection_enabled,
                        restart_required=True,
                    ),
                    SettingField(
                        "proxy_host",
                        Text(),
                        label="Host",
                        description="Proxy server hostname or IP address",
                        placeholder=DEFAULT_PROXY_HOST,
                        enabled_when=_connection_enabled,
                        restart_required=True,
                    ),
                    SettingField(
                        "proxy_port",
                        Integer(1, 65535),
                        label="Port",
                        description="Proxy server port number",
                        enabled_when=_connection_enabled,
                        restart_required=True,
                    ),
                    SettingField(
                        "proxy_auto_detect",
                        Toggle(),
                        label="Auto-enable proxy for *.lbl.gov URLs",
                        description=(
                            "When enabled, the proxy will be automatically used for URLs "
                            "ending in .lbl.gov, even if the main proxy toggle is off. "
                            "Useful for accessing LBNL internal services via SSH tunnel."
                        ),
                    ),
                    Action(
                        "Test Connection",
                        _test_connection,
                        enabled_when=_connection_enabled,
                        scopes=CONSOLE,
                    ),
                ],
            ),
            Section("Information", [Info(INFO_TEXT, scopes=CONSOLE)]),
        ]

    def validate_values(self, values: FormValues) -> list[str]:
        if _connection_enabled(values):
            host = values.effective("proxy_host", DEFAULT_PROXY_HOST)
            if not (host or "").strip():
                return ["Proxy host is required when the proxy or auto-detect is enabled"]
        return []
