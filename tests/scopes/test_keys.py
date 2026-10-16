"""Which preferences the scoped store manages."""
from __future__ import annotations

import pytest

from lightfall.scopes.keys import client_registry, flatten_preferences, is_local_only


@pytest.mark.parametrize("key", [
    "logbook_url", "proxy_host", "proxy_port", "ipc_nats_url", "code_editor",
    "recent_files", "claude_last_session_id", "ui.remember_geometry", "telemetry_dsn",
    "claude_api_key", "tiled_api_key", "tiled_alshub_api_key", "caproxy_token",
    "some_plugin_password", "_internal",
])
def test_local_only(key):
    assert is_local_only(key)


@pytest.mark.parametrize("key", [
    "theme", "font_size", "profile_image_id", "device_favorites", "visualization.colormap",
    "claude_model", "disabled_plugins", "tiled_url", "tiled_session_key_lifetime",
])
def test_scoped(key):
    assert not is_local_only(key)


def test_client_registry_declares_beamline_capable_keys():
    reg = client_registry()
    assert reg.get("tiled_url").allows("beamline")
    assert reg.get("device_favorites").allows("branch")
    assert reg.get("device_favorites").allows("user")
    assert not reg.is_declared("theme")
    assert reg.get("theme").scopes == frozenset({"user"})
    assert reg.get("plugins.enabled").policy == "union"  # core specs kept


def test_flatten_preferences_expands_dotted_groups_only():
    prefs = {
        "theme": "dark",
        "visualization": {"colormap": "viridis", "show_grid": True},
        "ui": {"remember_geometry": True},
        "monitor_feed_intervals": {"a": 5},
        "beamlines": {"7.0.1": {"default_data_dir": "/data"}},
    }
    assert flatten_preferences(prefs) == {
        "theme": "dark",
        "visualization.colormap": "viridis",
        "visualization.show_grid": True,
        "ui.remember_geometry": True,
        "monitor_feed_intervals": {"a": 5},
    }
