"""Which preferences the scoped store manages."""
from __future__ import annotations

import pytest

from lightfall.scopes.keys import (
    LOCAL_LAYER_KEYS,
    beamline_capable,
    client_registry,
    flatten_preferences,
    is_local_only,
)


@pytest.mark.parametrize("key", [
    "logbook_url", "proxy_host", "proxy_port", "ipc_nats_url", "code_editor",
    "recent_files", "claude_last_session_id", "ui.remember_geometry", "telemetry_dsn",
    "claude_api_key", "tiled_api_key", "tiled_alshub_api_key", "caproxy_token",
    "some_plugin_password", "_internal", "proxy_auto_detect_domains",
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


def test_client_registry_defers_to_core_specs():
    """lightfall-settings 0.1.3 declares tiled_url in core; the client must not re-register it."""
    from lightfall_settings import CORE_SPECS

    reg = client_registry()
    for spec in CORE_SPECS:
        assert reg.get(spec.key) == spec
    assert reg.get("tiled_url").allows("facility")
    assert reg.get("device_favorites").allows("branch")  # client specs still added


def test_lightfall_settings_has_the_public_flag():
    from lightfall_settings import SettingSpec

    assert SettingSpec("x").public is False


def test_client_registry_uses_deployment_specs():
    from lightfall_settings import DEPLOYMENT_SPECS

    reg = client_registry()
    for spec in DEPLOYMENT_SPECS:
        assert reg.get(spec.key) == spec
    assert not reg.get("tiled_beamline").allows("user")
    assert reg.get("device_backends").policy == "union"


def test_local_layer_keys_are_the_device_rows():
    assert LOCAL_LAYER_KEYS == {"device_backends", "device_backends_exclusive_restore"}


def test_beamline_capable():
    reg = client_registry()
    assert beamline_capable(reg.get("tiled_beamline"))
    assert beamline_capable(reg.get("device_favorites"))
    assert not beamline_capable(reg.get("theme"))
    assert not beamline_capable(reg.get("auth.realm"))  # facility only


def test_tiled_service_keys_are_site_wide_not_per_user():
    reg = client_registry()
    for key, tp in (("tiled_enabled", bool), ("tiled_auth_mode", str)):
        spec = reg.get(key)
        assert spec.scopes == {"facility", "beamline", "branch"}
        assert spec.type is tp
