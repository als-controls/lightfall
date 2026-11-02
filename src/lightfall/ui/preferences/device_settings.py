"""Settings -> Devices: connection settings, the CA tunnel and device backend rows, per scope.

A form page (see ``SettingsPlugin.sections``). The connection scalars may be set at
beamline, branch, this console or user scope; the CA tunnel keys are console-only. The
backend rows are the item-merged key ``device_backends``: each scope edits its own rows
and overrides of the rows broader scopes give it (``DeviceRowsEditor``), plus the
exclusive restore list at the same scope.

Saving applies the resolved rows to the running catalog, except while a plan runs or when
the rows switch the session mode (entering or leaving an exclusive backend), which takes a
restart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from lightfall_settings import SettingSpec

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import (
    DEVICE_BACKENDS_KEY,
    EXCLUSIVE_RESTORE_KEY,
    build_context,
    load_rows,
    rows_from_value,
)
from lightfall.devices.backend_types.session import (
    crosses_mode_boundary,
    current_session_mode,
    exclusive_row,
)
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.device_rows import device_rows_editor
from lightfall.ui.preferences.form.schema import (
    Choice,
    Custom,
    Number,
    Section,
    SettingField,
    Text,
    Toggle,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

    from lightfall.ui.preferences.form.schema import FormValues

_SCOPES = frozenset({"beamline", "branch", "user"})
_ROW_KEYS = (DEVICE_BACKENDS_KEY, EXCLUSIVE_RESTORE_KEY)

INSTANTIATE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("background", "Background (recommended)"),
    ("none", "On demand (manual)"),
    ("blocking", "Blocking (legacy)"),
)
BACKENDS_DESCRIPTION = (
    "Backend changes apply when you save. Edited backends reconnect their devices; "
    "connection settings apply to backends built after that."
)
PLAN_RUNNING_REASON = (
    "Device backends: changes were saved but not applied because a plan is running; "
    "restart Lightfall to apply them."
)


class DeviceSettingsPlugin(SettingsPlugin):
    """Settings plugin for device connection settings and backend rows."""

    @property
    def name(self) -> str:
        return "devices"

    @property
    def display_name(self) -> str:
        return "Devices"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 20

    def specs(self) -> Sequence[SettingSpec]:
        # The device_backends* keys are declared by lightfall-settings.
        return [
            SettingSpec(key="device_instantiate_mode", type=str, scopes=_SCOPES,
                        label="Device instantiation"),
            SettingSpec(key="device_connection_timeout", type=float, scopes=_SCOPES,
                        label="Connection timeout"),
            SettingSpec(key="device_connect_on_startup", type=bool, scopes=_SCOPES,
                        label="Connect devices on startup"),
        ]

    def sections(self) -> Sequence[Section]:
        return [
            Section(
                "Connection",
                [
                    SettingField(
                        "device_instantiate_mode", Choice(INSTANTIATE_OPTIONS),
                        label="Device instantiation",
                        description="Background connects devices in worker threads (fastest startup); "
                                    "on demand connects a device when it is selected; blocking connects "
                                    "all devices at startup (can be slow).",
                    ),
                    SettingField(
                        "device_connection_timeout", Number(0.5, 120, 0.5),
                        label="Connection timeout", unit="s", placeholder="5",
                        description="How long to wait for a device to connect before marking it failed. "
                                    "Increase it for slow devices or network latency.",
                    ),
                    SettingField(
                        "device_connect_on_startup", Toggle(),
                        label="Connect devices on startup",
                        description="On: devices start connecting when Lightfall starts. "
                                    "Off: they connect when you select them.",
                    ),
                ],
            ),
            Section(
                "Remote access (CA tunnel)",
                [
                    SettingField(
                        "ca_tunnel_enabled", Toggle(), label="Enable CA tunnel",
                        restart_required=True,
                        description="Bridges local Channel Access to a remote CA Gateway over an SSH "
                                    "tunnel (e.g. ssh -L 5099:localhost:5099 user@host).",
                    ),
                    SettingField(
                        "ca_tunnel_gateway", Text(), label="Gateway address",
                        placeholder="localhost:5099", restart_required=True,
                        enabled_when=lambda v: v.effective("ca_tunnel_enabled") is True,
                        description="The CA Gateway as host:port; match your SSH tunnel's local port.",
                    ),
                ],
            ),
            Section(
                "Backends",
                [
                    SettingField(
                        DEVICE_BACKENDS_KEY, Custom(device_rows_editor), label="Backends",
                        companions=(EXCLUSIVE_RESTORE_KEY,),
                    ),
                ],
                description=BACKENDS_DESCRIPTION,
            ),
        ]

    def validate_values(self, values: FormValues) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        registry = BackendTypeRegistry.get_instance()
        raw = values.get(DEVICE_BACKENDS_KEY)
        for row in rows_from_value(raw):
            # from_dict falls back to the type for a missing name: check the stored one.
            item = raw.get(row.id) if isinstance(raw, dict) else None
            if isinstance(item, dict) and not item.get("name"):
                row.name = ""
            if not row.name:
                errors.append("Every backend needs a name")
                continue
            key = row.name.lower()
            if key in seen:
                errors.append(f"Backend names must be unique: '{row.name}' is used twice")
            seen.add(key)
            backend_type = registry.get(row.type)
            if row.enabled and backend_type is not None:
                errors.extend(f"{row.name}: {e}" for e in backend_type.validate(row.config))
        # The catalog keys backends by name: an own row must not reuse an inherited row's
        # name either (inherited rows this scope overrides carry the own row's name).
        own_ids = set(raw) if isinstance(raw, dict) else set()
        inherited_names = {
            str(item.get("name", "")).lower()
            for rid, (item, _origin) in _inherited_rows(values).items()
            if rid not in own_ids and isinstance(item, dict)
        }
        for row in rows_from_value(raw):
            item = raw.get(row.id) if isinstance(raw, dict) else None
            name = item.get("name") if isinstance(item, dict) else row.name
            if name and str(name).lower() in inherited_names:
                errors.append(f"Backend names must be unique: '{name}' is used twice")
        return errors

    def after_save(self, scope: str, changed: Mapping[str, Any]) -> None:
        """Bring the running device catalog in line with the saved rows."""
        if not any(key in changed for key in _ROW_KEYS):
            return
        if _plan_active():
            # Removing a backend drops its devices; never do that under a plan. The dialog's
            # restart prompt says so (restart_reason).
            logger.warning("Device backend changes saved; not applied while a plan is running")
            return
        prefs = PreferencesManager.get_instance()
        rows = load_rows(prefs.get) or []
        if crosses_mode_boundary(rows, BackendTypeRegistry.get_instance()):
            return  # the EPICS environment is fixed per process: restart_reason asks
        from lightfall.devices.backend_types import startup

        startup.apply_backend_rows([deepcopy(row) for row in rows], build_context(prefs.get))

    def restart_reason(self, scope: str, changed: Mapping[str, Any]) -> str:
        if not any(key in changed for key in _ROW_KEYS):
            return ""
        if _plan_active():
            return PLAN_RUNNING_REASON  # after_save did not apply them
        registry = BackendTypeRegistry.get_instance()
        rows = load_rows(PreferencesManager.get_instance().get) or []
        if not crosses_mode_boundary(rows, registry):
            return ""
        entering = exclusive_row(rows, registry)
        if entering is not None:
            backend_type = registry.get(entering.type)
            label = backend_type.session_label(entering.config) if backend_type else entering.name
            return f"Device backends: Lightfall needs to restart to enter {label}."
        mode = current_session_mode()
        label = mode.label if mode else "this mode"
        return f"Device backends: Lightfall needs to restart to leave {label}."


def _inherited_rows(values: FormValues) -> dict[str, tuple[Any, str]]:
    """Rows broader scopes give the form's scope: row id -> (row dict, origin label)."""
    scope = getattr(values, "scope", None)
    if scope is None:
        return {}
    inherited_items = getattr(PreferencesManager.get_instance(), "inherited_items", None)
    if inherited_items is None:
        return {}
    return {str(rid): entry for rid, entry in (inherited_items(DEVICE_BACKENDS_KEY, scope) or {}).items()}


def _plan_active() -> bool:
    """Whether the acquisition engine is running, paused or stopping a plan."""
    import lightfall.acquire.engine as engine_mod
    from lightfall.acquire.engine import EngineState

    engine = engine_mod._engine
    return engine is not None and engine.state not in (EngineState.IDLE, EngineState.ERROR)
