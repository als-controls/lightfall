"""Built-in status bar plugins for NCS.

Provides the default status bar indicators:
- UserStatusPlugin: Shows current user and authentication state
- TiledStatusPlugin: Shows Tiled connection state
- ALSBeamStatusPlugin: Shows ALS synchrotron beam status
- ThreadStatusPlugin: Shows background task progress
- NatsStatusPlugin: Shows NATS IPC connection status and active peers
"""

from __future__ import annotations

from lightfall.ui.statusbar.plugins.als_beam_status import ALSBeamStatusPlugin
from lightfall.ui.statusbar.plugins.caproxy_lease_status import CaproxyLeaseStatusPlugin
from lightfall.ui.statusbar.plugins.nats_status import NatsStatusPlugin
from lightfall.ui.statusbar.plugins.thread_status import ThreadStatusPlugin
from lightfall.ui.statusbar.plugins.tiled_status import TiledStatusPlugin
from lightfall.ui.statusbar.plugins.user_status import UserStatusPlugin

__all__ = [
    "UserStatusPlugin",
    "TiledStatusPlugin",
    "ALSBeamStatusPlugin",
    "ThreadStatusPlugin",
    "NatsStatusPlugin",
    "CaproxyLeaseStatusPlugin",
]
