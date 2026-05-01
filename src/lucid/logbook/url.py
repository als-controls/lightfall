"""Resolve the logbook base URL once for any client that needs it.

LogbookClient and UserSettingsClient both talk to the same backend, so
both should ask this helper for the URL rather than reimplementing the
prefs lookup.
"""
from __future__ import annotations

DEFAULT_LOGBOOK_URL = "http://bcglucidlogbook.dhcp.lbl.gov"


def _load_pref() -> str | None:
    """Read the configured logbook URL from PreferencesManager.

    Returns None on any failure (manager uninitialised, ConfigManager
    missing, etc.). Wrapped so it's trivially monkeypatchable in tests.
    """
    from lucid.ui.preferences.manager import PreferencesManager
    prefs = PreferencesManager.get_instance()
    return prefs.get("logbook_url", None)


def get_logbook_base_url() -> str:
    """Return the configured logbook base URL, or the default fallback."""
    try:
        value = _load_pref()
    except Exception:
        return DEFAULT_LOGBOOK_URL
    return value or DEFAULT_LOGBOOK_URL
