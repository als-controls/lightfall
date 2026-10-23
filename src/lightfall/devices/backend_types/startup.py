"""Build the device catalog from ``device_backends`` rows, at startup and live.

:func:`install_backend_rows` runs once at startup. It leaves a
:class:`BackendInstaller` behind that remembers which row built which backend,
so :func:`apply_backend_rows` can bring the catalog in line with edited rows
(Settings -> Devices) without a restart.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lightfall.devices.backend_types.base import BackendType, BuildContext
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow

# The startup installer; apply_backend_rows() reconciles against it.
_INSTALLER: BackendInstaller | None = None


def _build(backend_type: BackendType, row: BackendRow, ctx: BuildContext) -> Any | None:
    try:
        backend = backend_type.build(row.config, row.name, ctx)
    except Exception as exc:
        logger.error("Could not build device backend '{}' ({}): {}", row.name, row.type, exc)
        return None
    logger.info(
        "Device backend '{}' ({}) enabled: {}", row.name, row.type, backend_type.summary(row.config)
    )
    return backend


def _fingerprint(row: BackendRow) -> str:
    """What makes a built backend stale: its type, name or config changed."""
    return json.dumps([row.type, row.name, row.config], sort_keys=True, default=str)


class BackendInstaller:
    """Owns the catalog backends built from ``device_backends`` rows.

    Rows whose type needs a login wait for the next AUTHENTICATED transition
    and are then added with ``catalog.add_and_connect_backend``.
    """

    def __init__(
        self, *, registry: BackendTypeRegistry, catalog: Any, ctx: BuildContext, session: Any
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._ctx = ctx
        self._session = session
        # row id -> (fingerprint, backend name) for backends in the catalog
        self._installed: dict[str, tuple[str, str]] = {}
        # row id -> (type, row) waiting for login
        self._deferred: dict[str, tuple[BackendType, BackendRow]] = {}
        self._login_hooked = False

    @property
    def deferred_rows(self) -> list[BackendRow]:
        return [row for _, row in self._deferred.values()]

    def install(self, rows: list[BackendRow]) -> list[BackendRow]:
        """Startup: ``catalog.add_backend`` each ready row (the caller connects them).

        Returns:
            The rows waiting for login (empty if none).
        """
        for backend_type, row in self._wanted(rows).values():
            self._place(backend_type, row, connect=False)
        self._hook_login_if_needed()
        return self.deferred_rows

    def apply(self, rows: list[BackendRow], ctx: BuildContext | None = None) -> None:
        """Live: remove backends whose row went away or changed, build the new ones.

        Unchanged rows keep their backend and live devices. ``ctx`` (global
        connection settings) applies to backends built from here on.
        """
        if ctx is not None:
            self._ctx = ctx
        wanted = self._wanted(rows)

        for row_id, (fingerprint, backend_name) in list(self._installed.items()):
            row = wanted.get(row_id, (None, None))[1]
            if row is None or _fingerprint(row) != fingerprint:
                logger.info("Removing device backend '{}' (row changed or removed)", backend_name)
                self._catalog.remove_backend(backend_name)
                del self._installed[row_id]
        for row_id, (_, old_row) in list(self._deferred.items()):
            row = wanted.get(row_id, (None, None))[1]
            if row is None or _fingerprint(row) != _fingerprint(old_row):
                del self._deferred[row_id]

        for row_id, (backend_type, row) in wanted.items():
            if row_id in self._installed or row_id in self._deferred:
                continue
            self._place(backend_type, row, connect=True)
        self._hook_login_if_needed()

    def _wanted(self, rows: list[BackendRow]) -> dict[str, tuple[BackendType, BackendRow]]:
        wanted: dict[str, tuple[BackendType, BackendRow]] = {}
        for row in rows:
            if not row.enabled:
                continue
            backend_type = self._registry.get(row.type)
            if backend_type is None:
                logger.warning(
                    "Device backend '{}' has unknown type '{}' (plugin not installed?); skipping",
                    row.name,
                    row.type,
                )
                continue
            wanted[row.id] = (backend_type, row)
        return wanted

    def _place(
        self, backend_type: BackendType, row: BackendRow, *, connect: bool, logged_in: bool = False
    ) -> None:
        if backend_type.requires_auth and not (logged_in or self._session.is_authenticated):
            self._deferred[row.id] = (backend_type, row)
            logger.info("Device backend '{}' will load after login", row.name)
            return
        backend = _build(backend_type, row, self._ctx)
        if backend is None:
            return
        if connect:
            self._catalog.add_and_connect_backend(backend)
        else:
            self._catalog.add_backend(backend)
        self._installed[row.id] = (_fingerprint(row), backend.name)

    def _hook_login_if_needed(self) -> None:
        if self._deferred and not self._login_hooked:
            self._session.state_changed.connect(self._on_state_changed)
            self._login_hooked = True

    def _on_state_changed(self, new_state: Any, _old_state: Any) -> None:
        from lightfall.auth.session import AuthState

        if new_state != AuthState.AUTHENTICATED:
            return
        self._session.state_changed.disconnect(self._on_state_changed)
        self._login_hooked = False
        deferred, self._deferred = self._deferred, {}
        for backend_type, row in deferred.values():
            self._place(backend_type, row, connect=True, logged_in=True)


def install_backend_rows(
    rows: list[BackendRow],
    *,
    registry: BackendTypeRegistry,
    catalog: Any,
    ctx: BuildContext,
    session: Any,
) -> list[BackendRow]:
    """Add each enabled row's backend to ``catalog``; defer login-only rows.

    Rows whose type needs a login are built on the first transition to
    AUTHENTICATED and added with ``catalog.add_and_connect_backend``. The caller
    still calls ``catalog.connect_backends()`` for the rows added now.

    Returns:
        The rows waiting for login (empty if none).
    """
    global _INSTALLER
    _INSTALLER = BackendInstaller(registry=registry, catalog=catalog, ctx=ctx, session=session)
    return _INSTALLER.install(rows)


def apply_backend_rows(rows: list[BackendRow], ctx: BuildContext | None = None) -> bool:
    """Bring the live catalog in line with edited rows. False if devices haven't started."""
    if _INSTALLER is None:
        return False
    _INSTALLER.apply(rows, ctx)
    return True
