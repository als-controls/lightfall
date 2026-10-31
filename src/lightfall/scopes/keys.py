"""Which preference keys the scoped store manages, and the client's own setting specs.

Every preference resolves through the scope chain (Ron, 2026-10-16) except local-only
keys: how this machine reaches servers, this machine's tools and history, and anything
that holds a credential. Local-only keys stay in this machine's YAML and are never
uploaded (spec section 1: the scoped store never holds secrets).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from lightfall_settings import (
    ALL_KINDS,
    SettingSpec,
    SpecRegistry,
    deployment_registry,
    spec_from_dict,
    user_only_spec,
)

from lightfall.utils.logging import logger

LOCAL_ONLY_KEYS: frozenset[str] = frozenset({
    # How this machine reaches servers (needed before login; differs per network).
    "logbook_url", "logbook_enabled", "logbook_offline_only",
    "proxy_enabled", "proxy_type", "proxy_host", "proxy_port", "proxy_auto_detect",
    "proxy_auto_detect_domains",
    "ca_tunnel_enabled", "ca_tunnel_gateway",
    "ipc_use_local_nats", "ipc_nats_url", "ipc_local_nats_port", "ipc_display_name",
    "services_local_port",
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


def console_only(key: str) -> bool:
    """True if ``key`` lives only in this console's layer (never in a server scope)."""
    return is_local_only(key)


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

# How a site reaches its services: set for everyone at facility/beamline/branch, never
# per user (Ron, 2026-10-30). Same rules as the server's approved specs.
_SITE_WIDE = frozenset({"facility", "beamline", "branch"})
_SITE_WIDE_KEYS: dict[str, type] = {"tiled_enabled": bool, "tiled_auth_mode": str}

CLIENT_SPECS: tuple[SettingSpec, ...] = (
    *(SettingSpec(key=k, scopes=ALL_KINDS) for k in _FACILITY_CAPABLE),
    *(SettingSpec(key=k, scopes=_BEAMLINE_AND_BELOW) for k in _BEAMLINE_CAPABLE),
    *(SettingSpec(key=k, type=tp, scopes=_SITE_WIDE) for k, tp in _SITE_WIDE_KEYS.items()),
)


_CLIENT_REGISTERED: dict[str, SettingSpec] = {}
# One cached registry per set of approved server specs (key: their JSON). Each entry
# also records the keys declared before page specs were added, for base_spec().
_REGISTRIES: dict[str, tuple[SpecRegistry, frozenset[str]]] = {}


def _specs_key(server_specs: tuple[Mapping[str, Any], ...]) -> str:
    return json.dumps(list(server_specs), sort_keys=True, default=str)


def _build_registry(server_specs: Iterable[Mapping[str, Any]]) -> SpecRegistry:
    reg = deployment_registry()
    for raw in server_specs:
        try:
            spec = spec_from_dict(raw)
        except ValueError as e:
            logger.warning("Ignoring an approved spec the client can't read: {}", e)
            continue
        if not reg.is_declared(spec.key):
            reg.register(spec)
    # Shared specs win: lightfall-settings declares tiled_url (core, public) and the
    # beamline identity keys and device rows (DEPLOYMENT_SPECS), which the server enforces.
    reg.register_all(spec for spec in CLIENT_SPECS if not reg.is_declared(spec.key))
    return reg


def _base_keys(reg: SpecRegistry) -> frozenset[str]:
    for cached, base in _REGISTRIES.values():
        if cached is reg:
            return base
    return frozenset()


def client_registry(server_specs: Iterable[Mapping[str, Any]] = ()) -> SpecRegistry:
    """The rules this client resolves with. ``server_specs`` are the logbook's approved
    specs (from /chain): they win over the client's own advisory ones, as the server
    enforces them. Specs registered by settings pages (``register_client_specs``) fill in
    keys nothing else declares.

    Cached per set of approved specs, and page registrations reach every cached registry,
    so a holder (``ScopedBackend``) sees specs registered after it captured one."""
    specs = tuple(server_specs)
    cache_key = _specs_key(specs)
    entry = _REGISTRIES.get(cache_key)
    if entry is None:
        reg = _build_registry(specs)
        base = frozenset(reg.keys())
        for spec in _CLIENT_REGISTERED.values():
            if spec.key not in base:
                reg.register(spec, replace=True)
        _REGISTRIES[cache_key] = entry = (reg, base)
    return entry[0]


def register_client_specs(specs: Iterable[SettingSpec]) -> None:
    """Declare client-side specs (e.g. from a settings plugin) in every client registry.

    Idempotent per key: a later registration replaces an earlier one. Keys the shared
    registry, the server's approved specs or CLIENT_SPECS declare keep their spec.
    """
    client_registry()  # make sure the default registry exists
    for spec in specs:
        _CLIENT_REGISTERED[spec.key] = spec
        for reg, base in _REGISTRIES.values():
            if spec.key not in base:
                reg.register(spec, replace=True)


def base_spec(key: str, registry: SpecRegistry | None = None) -> SettingSpec:
    """``key``'s spec without page registrations: shared/deployment, approved server specs
    and CLIENT_SPECS only.

    Specs a settings page registered describe where the page lets the key be edited, not
    what the key is, so they are ignored here; without them such a key is user-only.
    """
    reg = registry or client_registry()
    if key in _CLIENT_REGISTERED and key not in _base_keys(reg):
        return user_only_spec(key)
    return reg.get(key)


def _reset_client_registry() -> None:
    """Forget cached registries and registered specs (test isolation)."""
    _REGISTRIES.clear()
    _CLIENT_REGISTERED.clear()


# Keys with a writable console layer: the console's own YAML sits between branch and user
# scope (settings-layering design, Part 2). Everything else uses the YAML only as the
# fallback below every scope.
LOCAL_LAYER_KEYS: frozenset[str] = frozenset({
    "device_backends", "device_backends_exclusive_restore",
})


def beamline_capable(spec: SettingSpec) -> bool:
    """True if a beamline or branch may set this key (so it describes a deployment)."""
    return spec.allows("beamline") or spec.allows("branch")


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
