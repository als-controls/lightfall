"""Build the device catalog from ``device_backends`` rows at startup."""

from __future__ import annotations

from typing import Any

from loguru import logger

from lightfall.devices.backend_types.base import BackendType, BuildContext
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow

# Keeps one-shot login handlers alive (PySide holds only weak refs to some callables).
_PENDING_LOGIN_HANDLERS: list[Any] = []


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
    deferred: list[tuple[BackendType, BackendRow]] = []
    for row in rows:
        if not row.enabled:
            continue
        backend_type = registry.get(row.type)
        if backend_type is None:
            logger.warning(
                "Device backend '{}' has unknown type '{}' (plugin not installed?); skipping",
                row.name,
                row.type,
            )
            continue
        if backend_type.requires_auth and not session.is_authenticated:
            deferred.append((backend_type, row))
            continue
        backend = _build(backend_type, row, ctx)
        if backend is not None:
            catalog.add_backend(backend)

    if deferred:
        _defer_until_login(deferred, catalog=catalog, ctx=ctx, session=session)
    return [row for _, row in deferred]


def _defer_until_login(
    deferred: list[tuple[BackendType, BackendRow]], *, catalog: Any, ctx: BuildContext, session: Any
) -> None:
    from lightfall.auth.session import AuthState

    logger.info(
        "Device backend(s) {} will load after login", ", ".join(row.name for _, row in deferred)
    )

    def _on_state_changed(new_state: Any, _old_state: Any) -> None:
        if new_state != AuthState.AUTHENTICATED:
            return
        session.state_changed.disconnect(_on_state_changed)
        if _on_state_changed in _PENDING_LOGIN_HANDLERS:
            _PENDING_LOGIN_HANDLERS.remove(_on_state_changed)
        for backend_type, row in deferred:
            backend = _build(backend_type, row, ctx)
            if backend is not None:
                catalog.add_and_connect_backend(backend)

    _PENDING_LOGIN_HANDLERS.append(_on_state_changed)
    session.state_changed.connect(_on_state_changed)
