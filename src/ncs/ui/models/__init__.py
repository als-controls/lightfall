"""Qt models for NCS UI.

This package provides:
- DeviceTreeModel: Hierarchical model for ophyd device trees
- DeviceFilterProxyModel: Filter proxy for searching devices
- ThreadTableModel: Table model for background thread monitoring
- ThreadFilterProxyModel: Filter proxy for thread filtering
"""

from ncs.ui.models.device_tree import (
    DeviceFilterProxyModel,
    DeviceTreeItem,
    DeviceTreeModel,
    NodeType,
)
from ncs.ui.models.thread_model import (
    ThreadFilterProxyModel,
    ThreadManagerObserver,
    ThreadRecord,
    ThreadStatus,
    ThreadTableModel,
)

__all__ = [
    # Device models
    "DeviceFilterProxyModel",
    "DeviceTreeItem",
    "DeviceTreeModel",
    "NodeType",
    # Thread models
    "ThreadFilterProxyModel",
    "ThreadManagerObserver",
    "ThreadRecord",
    "ThreadStatus",
    "ThreadTableModel",
]
