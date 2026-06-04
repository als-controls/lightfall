"""Built-in status bar plugins for NCS.

Provides the default status bar indicators:
- UserStatusPlugin: Shows current user
- AuthStatusPlugin: Shows authentication state
- ConnectionStatusPlugin: Shows online/offline status
- TiledStatusPlugin: Shows Tiled connection state
- ALSBeamStatusPlugin: Shows ALS synchrotron beam status
- ThreadStatusPlugin: Shows background task progress
"""

from __future__ import annotations

from lightfall.ui.statusbar.plugins.als_beam_status import ALSBeamStatusPlugin
from lightfall.ui.statusbar.plugins.auth_status import AuthStatusPlugin
from lightfall.ui.statusbar.plugins.connection_status import ConnectionStatusPlugin
from lightfall.ui.statusbar.plugins.thread_status import ThreadStatusPlugin
from lightfall.ui.statusbar.plugins.tiled_status import TiledStatusPlugin
from lightfall.ui.statusbar.plugins.user_status import UserStatusPlugin

__all__ = [
    "UserStatusPlugin",
    "AuthStatusPlugin",
    "ConnectionStatusPlugin",
    "TiledStatusPlugin",
    "ALSBeamStatusPlugin",
    "ThreadStatusPlugin",
]
