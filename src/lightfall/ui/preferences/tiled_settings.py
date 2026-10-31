"""Tiled data catalog settings page.

Where each key lives (scope registry): enable and authentication mode are site-wide
(facility/beamline/branch); the server and alshub URLs may be set at any scope; the
API key is this console's own; the session key lifetime and the admin access override
are per user; the beamline follows the console's binding (editable at "This console"
when unbound, see form/binding.py).

Saving needs no hook here: ``setup.services.watch_tiled_preferences`` re-applies the
Tiled configuration whenever ``tiled_enabled``/``tiled_url``/``tiled_auth_mode``/
``tiled_api_key`` change, and the other keys are read when they are used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.services.tiled_service import DEFAULT_TILED_URL, TILED_DEFAULTS
from lightfall.ui.preferences.form.binding import binding_derived
from lightfall.ui.preferences.form.schema import (
    Action,
    Choice,
    FormValues,
    Secret,
    Section,
    SettingField,
    Text,
    Toggle,
    Url,
)
from lightfall.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

USER = frozenset({"user"})
DEFAULT_ALSHUB_URL = "https://bcgmds01.als.lbl.gov"
OVERRIDE_SECTION = "Access Override (Admin / Staff Only)"
AUTH_MODES: tuple[tuple[str, str], ...] = (
    ("", "Automatic (match sign-in)"),
    ("none", "None"),
    ("api_key", "API Key"),
    ("keycloak", "Keycloak (use Lightfall session)"),
)
KEY_LIFETIMES: tuple[tuple[int, str], ...] = (
    (604800, "7 days (default)"),
    (86400, "1 day"),
    (3600, "1 hour (development)"),
)
URL_REQUIRED = "Tiled server URL is required when enabled"
URL_INVALID = "Tiled server URL must start with http:// or https://"


@dataclass
class AccessOverride:
    """Time-windowed admin override of write-time ESAF selection."""

    esaf_id: str
    start: datetime
    end: datetime
    set_by: str | None = None


def access_override_from_prefs(prefs: PreferencesManager) -> AccessOverride | None:
    """Read AccessOverride from preferences, or None if any field missing."""
    esaf = prefs.get("tiled_access_override_esaf_id", "")
    start = prefs.get("tiled_access_override_start", "")
    end = prefs.get("tiled_access_override_end", "")
    set_by = prefs.get("tiled_access_override_set_by", None) or None
    if not (esaf and start and end):
        return None
    try:
        return AccessOverride(
            esaf_id=esaf,
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
            set_by=set_by,
        )
    except ValueError:
        return None


def _enabled(values: FormValues) -> bool:
    return bool(values.effective("tiled_enabled", TILED_DEFAULTS["tiled_enabled"]))


def _api_key_mode(values: FormValues) -> bool:
    return values.effective("tiled_auth_mode", TILED_DEFAULTS["tiled_auth_mode"]) == "api_key"


def _lifetime_options() -> list[tuple[int, str]]:
    """The presets, plus a hand-edited stored lifetime shown as "<n> seconds (custom)"."""
    options = list(KEY_LIFETIMES)
    stored = PreferencesManager.get_instance().get("tiled_session_key_lifetime")
    if isinstance(stored, int) and not isinstance(stored, bool) and stored not in dict(options):
        options.append((stored, f"{stored} seconds (custom)"))
    return options


def _is_valid_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _test_connection(values: FormValues) -> str:
    """Try the effective URL, auth mode and API key (worker thread); raise on failure."""
    from lightfall.services.tiled_service import TiledService

    url = (values.effective("tiled_url", TILED_DEFAULTS["tiled_url"]) or "").strip()
    if not url:
        raise RuntimeError("Enter a URL first")
    api_key = (values.effective("tiled_api_key", "") or "").strip() or None
    auth_mode = values.effective("tiled_auth_mode", TILED_DEFAULTS["tiled_auth_mode"]) or ""
    success, message = TiledService.get_instance().test_connection(url, api_key, auth_mode=auth_mode)
    if not success:
        raise RuntimeError(message)
    return "Connected!"


_BEAMLINE_FIELD, _BEAMLINE_BINDING = binding_derived(
    SettingField("tiled_beamline", Text(), label="Beamline", placeholder="e.g. 7.0.1.1")
)


class TiledSettingsPlugin(SettingsPlugin):
    """Settings page for the Tiled data catalog connection and per-entry authorization."""

    @property
    def name(self) -> str:
        return "tiled"

    @property
    def display_name(self) -> str:
        return "Tiled Data Catalog"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "data"

    @property
    def priority(self) -> int:
        return 50

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key="tiled_session_key_lifetime", scopes=USER, label="Session key lifetime"),
            SettingSpec(key="tiled_access_override_esaf_id", scopes=USER, label="Access override ESAF ID"),
            SettingSpec(key="tiled_access_override_start", scopes=USER, label="Access override start"),
            SettingSpec(key="tiled_access_override_end", scopes=USER, label="Access override end"),
        ]

    def _user_has_admin_or_staff(self) -> bool:
        """Check current SessionManager user claims for admin/staff groups.

        Reads from ``session.user.attributes`` (decoded JWT claims) rather
        than ``session.token`` (which is the raw JWT string and has no
        ``.claims`` attribute).
        """
        try:
            from lightfall.auth.session import SessionManager

            session = SessionManager.get_instance().session
            if not session or not session.user:
                return False
            groups = session.user.attributes.get("groups", []) or []
            if "tiled:admin" in groups:
                return True
            return any(g.startswith("staff:") for g in groups)
        except Exception:
            return False

    def _show_override(self, _values: FormValues) -> bool:
        return self._user_has_admin_or_staff()

    def sections(self) -> Sequence[Section]:
        override = self._show_override
        return [
            Section(
                "Tiled Connection",
                [
                    SettingField("tiled_enabled", Toggle(), label="Enable Tiled integration"),
                    SettingField(
                        "tiled_url",
                        Url(),
                        label="Server URL",
                        placeholder=DEFAULT_TILED_URL,
                        enabled_when=_enabled,
                    ),
                    SettingField("tiled_auth_mode", Choice(AUTH_MODES), label="Authentication"),
                    SettingField(
                        "tiled_api_key",
                        Secret(),
                        label="API Key",
                        placeholder="(optional)",
                        visible_when=_api_key_mode,
                    ),
                    SettingField(
                        "tiled_session_key_lifetime",
                        Choice(_lifetime_options),
                        label="Session key lifetime",
                        description=(
                            "TTL of the per-service API keys minted at login. The long default lets "
                            "detached long-running experiments finish; use a short lifetime when "
                            "restarting Lightfall frequently so stale keys don't accumulate against "
                            "the server's per-principal cap. Applies at next login."
                        ),
                    ),
                    Action("Test Connection", _test_connection, enabled_when=_enabled),
                ],
                description=(
                    "Tiled provides a data catalog for storing and accessing bluesky run data. "
                    "When enabled, acquisition data is automatically streamed to the Tiled server."
                ),
            ),
            Section(
                "Per-entry authorization (alshub-api)",
                [
                    _BEAMLINE_FIELD,
                    _BEAMLINE_BINDING,
                    SettingField(
                        "tiled_alshub_url", Url(), label="alshub URL", placeholder=DEFAULT_ALSHUB_URL
                    ),
                ],
                description=(
                    "Set Beamline + alshub URL to enable AccessStamper. Each Tiled entry written "
                    "after reconnect is stamped with an access_blob containing the operator's "
                    "Keycloak identity and the active ESAF for this beamline (looked up via "
                    "alshub-api's public active-esaf route). Off-network setups: configure SOCKS "
                    "in Network Proxy."
                ),
            ),
            Section(
                OVERRIDE_SECTION,
                [
                    SettingField(
                        "tiled_access_override_esaf_id",
                        Text(),
                        label="ESAF ID",
                        placeholder="BLS-00480-001",
                        visible_when=override,
                    ),
                    SettingField(
                        "tiled_access_override_start",
                        Text(),
                        label="Start (ISO8601)",
                        placeholder="2026-04-26T18:00:00+00:00",
                        visible_when=override,
                    ),
                    SettingField(
                        "tiled_access_override_end",
                        Text(),
                        label="End (ISO8601)",
                        placeholder="2026-04-27T02:00:00+00:00",
                        visible_when=override,
                    ),
                ],
                description=(
                    "When set and the current time is within [start, end], the AccessStamper "
                    "uses this ESAF ID instead of querying alshub-api."
                ),
            ),
        ]

    def validate_values(self, values: FormValues) -> list[str]:
        # Only an edit at this scope to the enable switch or the URL can make the pair
        # invalid here; another scope's state never blocks this save.
        is_dirty = getattr(values, "is_dirty", None)
        if not is_dirty or not (is_dirty("tiled_enabled") or is_dirty("tiled_url")):
            return []
        if not _enabled(values):
            return []
        url = (values.effective("tiled_url", TILED_DEFAULTS["tiled_url"]) or "").strip()
        if not url:
            return [URL_REQUIRED]
        if not _is_valid_url(url):
            return [URL_INVALID]
        return []
