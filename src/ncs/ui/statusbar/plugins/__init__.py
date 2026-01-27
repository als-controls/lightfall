"""Built-in status bar plugins for NCS.

Provides the default status bar indicators:
- UserStatusPlugin: Shows current user
- AuthStatusPlugin: Shows authentication state
- ConnectionStatusPlugin: Shows online/offline status
- TiledStatusPlugin: Shows Tiled connection state
"""

from __future__ import annotations

from ncs.ui.statusbar.plugins.auth_status import AuthStatusPlugin
from ncs.ui.statusbar.plugins.connection_status import ConnectionStatusPlugin
from ncs.ui.statusbar.plugins.tiled_status import TiledStatusPlugin
from ncs.ui.statusbar.plugins.user_status import UserStatusPlugin

__all__ = [
    "UserStatusPlugin",
    "AuthStatusPlugin",
    "ConnectionStatusPlugin",
    "TiledStatusPlugin",
]
