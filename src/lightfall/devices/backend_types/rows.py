"""Device backend rows: the ``device_backends`` preference and its migration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from loguru import logger

from lightfall.devices.backend_types.base import BuildContext

DEVICE_BACKENDS_KEY = "device_backends"

Getter = Callable[[str, Any], Any]
_UNSET = object()


@dataclass
class BackendRow:
    """One configured device backend: a type plus its own settings."""

    type: str
    name: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "enabled": self.enabled,
            "config": dict(self.config),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackendRow:
        return cls(
            type=str(data["type"]),
            name=str(data.get("name") or data["type"]),
            enabled=bool(data.get("enabled", True)),
            config=dict(data.get("config") or {}),
            id=str(data.get("id") or uuid4().hex),
        )


def resolve_enabled_backends(get: Any) -> tuple[bool, bool, bool]:
    """Decide which built-in device backends are enabled, from a prefs ``get``.

    ``get`` is a ``prefs.get(key, default)``-style callable. Returns
    ``(mock_enabled, bcs_enabled, happi_enabled)``.

    Rules:
    * Explicit ``device_{mock,bcs,happi}_enabled`` flags win verbatim.
    * Absent flags fall back to legacy ``device_backend`` ("mock"/"bcs").
    * The mock fallback fires ONLY for a fresh config where the user has
      expressed no backend preference at all (no flags AND no legacy key) — so a
      deliberate all-off (e.g. relying solely on a plugin-contributed backend
      like the CMS happi backend) is honored instead of being forced back to
      mock. This is the bug fix: previously an explicit all-false re-enabled mock.
    """
    legacy_backend = get("device_backend", _UNSET)
    legacy_is_mock = legacy_backend == "mock" if legacy_backend is not _UNSET else False
    legacy_is_bcs = legacy_backend == "bcs" if legacy_backend is not _UNSET else False
    mock_enabled = bool(get("device_mock_enabled", legacy_is_mock))
    bcs_enabled = bool(get("device_bcs_enabled", legacy_is_bcs))
    happi_enabled = bool(get("device_happi_enabled", False))

    no_preference = (
        legacy_backend is _UNSET
        and get("device_mock_enabled", _UNSET) is _UNSET
        and get("device_bcs_enabled", _UNSET) is _UNSET
        and get("device_happi_enabled", _UNSET) is _UNSET
    )
    if no_preference and not any([mock_enabled, bcs_enabled, happi_enabled]):
        mock_enabled = True
    return mock_enabled, bcs_enabled, happi_enabled


def migrate_legacy(get: Getter) -> list[BackendRow]:
    """Turn the pre-2026-10 ``device_{mock,bcs,happi}_*`` keys into rows.

    The mock row is always created (enabled per :func:`resolve_enabled_backends`).
    BCS and happi rows are created when enabled or when the user ever set their
    address/path, so a disabled-but-configured backend keeps its settings.
    """
    mock_on, bcs_on, happi_on = resolve_enabled_backends(get)
    rows = [
        BackendRow(
            type="mock",
            name="mock",
            enabled=mock_on,
            config={"include_noisy": bool(get("device_mock_include_noisy", True))},
        )
    ]
    if bcs_on or get("device_bcs_host", _UNSET) is not _UNSET:
        rows.append(
            BackendRow(
                type="bcs",
                name="bcs",
                enabled=bcs_on,
                config={
                    "host": get("device_bcs_host", "localhost") or "localhost",
                    "port": int(get("device_bcs_port", 5577)),
                    "beamline": get("device_bcs_beamline", "") or "",
                    "timeout_ms": int(get("device_bcs_timeout_ms", 5000)),
                },
            )
        )
    if happi_on or get("device_happi_path", _UNSET) is not _UNSET:
        rows.append(
            BackendRow(
                type="happi",
                name="happi",
                enabled=happi_on,
                config={
                    "path": get("device_happi_path", "") or "",
                    "beamline": get("device_happi_beamline", "") or "",
                },
            )
        )
    return rows


def load_rows(get: Getter) -> list[BackendRow] | None:
    """Read saved rows; ``None`` when the preference has never been written."""
    raw = get(DEVICE_BACKENDS_KEY, None)
    if raw is None:
        return None
    rows: list[BackendRow] = []
    for entry in raw:
        try:
            rows.append(BackendRow.from_dict(entry))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Ignoring malformed device backend row {!r}: {}", entry, exc)
    return rows


def save_rows(prefs: Any, rows: Iterable[BackendRow]) -> None:
    prefs.set(DEVICE_BACKENDS_KEY, [row.to_dict() for row in rows])


def load_or_migrate(prefs: Any) -> list[BackendRow]:
    """Saved rows, or (first run after upgrade) migrated legacy rows, saved."""
    rows = load_rows(prefs.get)
    if rows is None:
        rows = migrate_legacy(prefs.get)
        save_rows(prefs, rows)
        logger.info("Migrated legacy device backend preferences into {} row(s)", len(rows))
    return rows


def unique_name(base: str, existing: Iterable[str]) -> str:
    """``base``, or ``base-2``, ``base-3``... whichever is free (case-insensitive)."""
    taken = {name.lower() for name in existing}
    if base.lower() not in taken:
        return base
    index = 2
    while f"{base}-{index}".lower() in taken:
        index += 1
    return f"{base}-{index}"


def build_context(get: Getter) -> BuildContext:
    """Global connection settings, with the legacy happi-instantiate fallback."""
    mode = get("device_instantiate_mode", None)
    if mode is None:
        mode = "blocking" if get("device_happi_instantiate", False) else "none"
    return BuildContext(
        instantiate_mode=str(mode),
        connection_timeout=float(get("device_connection_timeout", 5.0)),
    )
