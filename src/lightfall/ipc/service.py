"""Lightfall's IPC service -- the implementation lives in ``lightfall_utils.ipc``."""

from __future__ import annotations

from lightfall_utils.ipc.service import (  # noqa: F401
    ActionInfo,
    EventInfo,
    IPCService,
    _ActionHandle,
    _SessionChannel,
    _Subscription,
)

__all__ = ["IPCService", "ActionInfo", "EventInfo", "_ActionHandle", "get_ipc_service"]


def get_ipc_service() -> IPCService | None:
    """The application's IPCService, via :class:`ServiceRegistry`, or None."""
    from lightfall.core.services import ServiceRegistry

    return ServiceRegistry.get_instance().get(IPCService, None)
