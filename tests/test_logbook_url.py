"""Test the logbook base-URL lookup helper."""
from __future__ import annotations

import pytest


def test_default_when_no_pref(monkeypatch):
    """If PreferencesManager isn't initialised or has no value, return the
    fallback base URL."""
    from lightfall.logbook.url import get_logbook_base_url, DEFAULT_LOGBOOK_URL
    import lightfall.logbook.url as mod

    def boom():
        raise RuntimeError("no prefs in test")

    monkeypatch.setattr(mod, "_load_pref", boom)
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL


def test_pref_value_overrides_default(monkeypatch):
    from lightfall.logbook.url import get_logbook_base_url
    import lightfall.logbook.url as mod

    monkeypatch.setattr(mod, "_load_pref", lambda: "https://custom.example/lb")
    assert get_logbook_base_url() == "https://custom.example/lb"


def test_pref_returning_empty_falls_back(monkeypatch):
    """A blank/None pref must yield the default, not an empty URL."""
    from lightfall.logbook.url import get_logbook_base_url, DEFAULT_LOGBOOK_URL
    import lightfall.logbook.url as mod

    monkeypatch.setattr(mod, "_load_pref", lambda: "")
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL
    monkeypatch.setattr(mod, "_load_pref", lambda: None)
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL
