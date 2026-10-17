"""Which preference keys the scoped store manages, and the client's own setting specs.

Every preference resolves through the scope chain (Ron, 2026-10-16) except local-only
keys: how this machine reaches servers, this machine's tools and history, and anything
that holds a credential. Local-only keys stay in this machine's YAML and are never
uploaded (spec section 1: the scoped store never holds secrets).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lightfall_settings import ALL_KINDS, SettingSpec, SpecRegistry, core_registry

LOCAL_ONLY_KEYS: frozenset[str] = frozenset({
    # How this machine reaches servers (needed before login; differs per network).
    "logbook_url", "logbook_enabled", "logbook_offline_only",
    "proxy_enabled", "proxy_type", "proxy_host", "proxy_port", "proxy_auto_detect",
    "ca_tunnel_enabled", "ca_tunnel_gateway",
    "ipc_use_local_nats", "ipc_nats_url", "ipc_local_nats_port", "ipc_display_name",
    "telemetry_dsn",
    # This machine's tools, paths and history.
    "code_editor", "suppress_pycharm_warning", "device_happi_path", "recent_files",
    "claude_last_session_id", "agy_last_session_id", "agy_path", "ui.remember_geometry",
})

_SECRET_MARKERS = ("api_key", "token", "secret", "password", "dsn")


def is_local_only(key: str) -> bool:
    lowered = key.lower()
    return (
        key in LOCAL_ONLY_KEYS
        or key.startswith("_")
        or any(marker in lowered for marker in _SECRET_MARKERS)
    )


_BEAMLINE_AND_BELOW = frozenset({"beamline", "branch", "user"})

# Keys a facility, beamline or branch may set for everyone there (users can still
# override them). Advisory on the client; the server enforces only approved specs.
_FACILITY_CAPABLE = ("tiled_url", "tiled_alshub_url", "caproxy_url")
_BEAMLINE_CAPABLE = (
    "default_data_dir", "panel_layout", "plot_defaults", "acquisition_defaults",
    "device_favorites", "tiled_beamline", "current_beamline", "ipc_topic_prefix",
    "device_backend", "device_mock_enabled", "device_bcs_enabled", "device_bcs_host",
    "device_bcs_port", "device_bcs_beamline", "device_bcs_timeout_ms",
    "device_happi_enabled", "device_happi_beamline", "device_happi_instantiate",
)

CLIENT_SPECS: tuple[SettingSpec, ...] = (
    *(SettingSpec(key=k, scopes=ALL_KINDS) for k in _FACILITY_CAPABLE),
    *(SettingSpec(key=k, scopes=_BEAMLINE_AND_BELOW) for k in _BEAMLINE_CAPABLE),
)


def client_registry() -> SpecRegistry:
    reg = core_registry()
    reg.register_all(CLIENT_SPECS)
    return reg


# Preference groups stored nested in YAML but addressed as dotted keys
# ("visualization.colormap" lives at preferences.visualization.colormap).
_DOTTED_GROUPS = frozenset({"visualization", "ui"})


def flatten_preferences(prefs: Mapping[str, Any]) -> dict[str, Any]:
    """YAML ``preferences`` mapping -> {preference key: value}.

    ``beamlines`` (per-beamline YAML overrides) is dropped: beamline values now come
    from beamline scope.
    """
    out: dict[str, Any] = {}
    for key, value in prefs.items():
        if key == "beamlines":
            continue
        if key in _DOTTED_GROUPS and isinstance(value, Mapping):
            for sub, sub_value in value.items():
                out[f"{key}.{sub}"] = sub_value
        else:
            out[key] = value
    return out
