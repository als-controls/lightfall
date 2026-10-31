"""Logbook backend settings plugin.

Configures the connection to the lightfall-logbook backend service
for experiment logbook persistence and sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.logbook.url import DEFAULT_LOGBOOK_URL
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Action,
    FormValues,
    Section,
    SettingField,
    Toggle,
    Url,
)
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

# Every key on this page is console-only, so everything is shown at "This console".
CONSOLE = frozenset({"local"})


def _enabled(values: FormValues) -> bool:
    return values.effective("logbook_enabled", True) is not False


def _test_connection(values: FormValues) -> str:
    """GET ``{url}/health`` (worker thread); raise to show the failure."""
    url = (values.effective("logbook_url", DEFAULT_LOGBOOK_URL) or "").strip()
    if not url:
        raise ValueError("Enter a URL first")

    import httpx

    # Use proxy settings if configured
    client_kwargs: dict = {"timeout": 5.0}
    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

        proxy_url = ProxySettingsProvider.should_use_proxy_for_url(url)
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
    except Exception:
        pass

    try:
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(f"{url.rstrip('/')}/health")
    except Exception as e:
        logger.debug("Logbook test connection failed: {}", e)
        raise RuntimeError(f"Unreachable: {e}") from e
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return "Server reachable ✓"


class LogbookSettingsPlugin(SettingsPlugin):
    """Settings plugin for logbook backend configuration.

    Allows users to configure:
    - Enable/disable logbook backend sync
    - Backend server URL
    - Test connection functionality
    - Offline mode behavior

    Preferences keys:
    - ``logbook_enabled``: bool - whether backend sync is active
    - ``logbook_url``: str - base URL of the lightfall-logbook service
    - ``logbook_offline_only``: bool - force offline-only mode (local SQLite only)
    """

    @property
    def name(self) -> str:
        return "logbook"

    @property
    def display_name(self) -> str:
        return "Logbook"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "data"

    @property
    def priority(self) -> int:
        return 55

    def sections(self) -> Sequence[Section]:
        return [
            Section(
                "Logbook Backend",
                [
                    SettingField(
                        "logbook_enabled",
                        Toggle(),
                        label="Enable logbook backend sync",
                        description=(
                            "The logbook backend (lightfall-logbook) stores experiment notes "
                            "and system-generated fragments. When disabled or unreachable, "
                            "data is stored locally and synced when the connection is restored."
                        ),
                    ),
                    SettingField(
                        "logbook_url",
                        Url(),
                        label="Server URL",
                        placeholder=DEFAULT_LOGBOOK_URL,
                        enabled_when=_enabled,
                    ),
                    Action("Test Connection", _test_connection, enabled_when=_enabled,
                           scopes=CONSOLE),
                ],
            ),
            Section(
                "Offline Mode",
                [
                    SettingField(
                        "logbook_offline_only",
                        Toggle(),
                        label="Force offline-only mode (no server sync)",
                        description=(
                            "When on, the logbook uses only local SQLite storage "
                            "and never attempts to sync with the backend. Useful for "
                            "standalone or disconnected operation."
                        ),
                    ),
                ],
            ),
        ]

    def validate_values(self, values: FormValues) -> list[str]:
        enabled = values.effective("logbook_enabled", True) is not False
        offline = values.effective("logbook_offline_only", False) is True
        if enabled and not offline:
            url = (values.effective("logbook_url", DEFAULT_LOGBOOK_URL) or "").strip()
            if not url:
                return ["Logbook server URL is required when sync is enabled"]
        return []
