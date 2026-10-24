"""The session mode: the one enabled row of an exclusive backend type, if any.

It is resolved once at startup (its environment must be in place before any
EPICS client exists) and stays fixed for the life of the process. The device
installer, the plugin loader, Settings -> Devices and the main window all ask
:func:`current_session_mode`; a row edit that would change it takes a restart
(:func:`crosses_mode_boundary`).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass, field

from loguru import logger

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow, Getter, load_rows


@dataclass(frozen=True)
class SessionMode:
    """An exclusive row in charge of this session."""

    row_id: str
    type_id: str
    label: str
    env: dict[str, str] = field(default_factory=dict)


_MODE: SessionMode | None = None


def _session_parts(
    row: BackendRow, registry: BackendTypeRegistry
) -> tuple[str, dict[str, str]] | None:
    """(label, env) for an exclusive row, or None if its type can't produce them."""
    backend_type = registry.get(row.type)
    if backend_type is None:
        return None
    try:
        return backend_type.session_label(row.config), dict(backend_type.session_env(row.config))
    except Exception as exc:
        logger.error(
            "Device backend '{}' ({}) can't set up its session: {}", row.name, row.type, exc
        )
        return None


def is_exclusive(row: BackendRow, registry: BackendTypeRegistry) -> bool:
    backend_type = registry.get(row.type)
    return backend_type is not None and backend_type.exclusive


def exclusive_row(rows: list[BackendRow], registry: BackendTypeRegistry) -> BackendRow | None:
    """The first enabled, usable row whose registered type is exclusive, or None.

    A row whose type fails to give its session label/env is unusable (logged).
    Warns when more than one usable one is enabled (hand-edited preferences);
    only the first counts.
    """
    found = [
        row
        for row in rows
        if row.enabled and is_exclusive(row, registry) and _session_parts(row, registry)
    ]
    if len(found) > 1:
        logger.warning(
            "Only one exclusive device backend can be on; using '{}', ignoring {}",
            found[0].name,
            ", ".join(f"'{row.name}'" for row in found[1:]),
        )
    return found[0] if found else None


def resolve_session_mode(
    rows: list[BackendRow], registry: BackendTypeRegistry
) -> SessionMode | None:
    """The session mode ``rows`` call for, or None for a normal session."""
    row = exclusive_row(rows, registry)
    if row is None:
        return None
    parts = _session_parts(row, registry)
    if parts is None:
        return None
    label, env = parts
    return SessionMode(row_id=row.id, type_id=row.type, label=label, env=env)


def current_session_mode() -> SessionMode | None:
    """The mode this process started in (None: a normal session)."""
    return _MODE


def set_session_mode(mode: SessionMode | None) -> None:
    global _MODE
    _MODE = mode


def crosses_mode_boundary(rows: list[BackendRow], registry: BackendTypeRegistry) -> bool:
    """Whether ``rows`` want a different session mode than the one running.

    A different exclusive row (or none), or the same row asking for a
    different environment: either needs a fresh process.
    """
    wanted = resolve_session_mode(rows, registry)
    current = current_session_mode()

    def key(mode: SessionMode | None) -> tuple[str, dict[str, str]] | None:
        return (mode.row_id, mode.env) if mode else None

    return key(wanted) != key(current)


def start_session_mode(
    get: Getter,
    registry: BackendTypeRegistry,
    environ: MutableMapping[str, str] = os.environ,
) -> SessionMode | None:
    """Startup: decide the session mode from the saved rows and apply its env.

    Must run before any EPICS client context exists. Never migrates legacy
    preferences (a fresh install has no exclusive row).
    """
    rows = load_rows(get)
    mode = resolve_session_mode(rows, registry) if rows else None
    if mode is not None:
        environ.update(mode.env)
        logger.info("Session mode: {}", mode.label)
    set_session_mode(mode)
    return mode
