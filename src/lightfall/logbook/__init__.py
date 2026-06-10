"""
Experiment logbook integration for Lightfall.

This package provides:
- Logbook service client (``lightfall.logbook.client``)
- Fragment-based entry widgets (``lightfall.logbook.entry_widget``,
  ``lightfall.logbook.fragment_widgets``)
- Logbook event listening (``lightfall.logbook.event_listener``)
- Logbook URL helpers (``lightfall.logbook.url``)
- Automatic device action logging

Device action logging:
    >>> from lightfall.logbook import DeviceActionLogger
    >>> logger = DeviceActionLogger.get_instance()
    >>> logger.connect_to_control_widget(motor_widget)
    >>> logger.action_recorded.connect(on_action)
"""

from lightfall.logbook.action_logger import ActionGroup, DeviceAction, DeviceActionLogger
from lightfall.logbook.client import LogbookClient

__all__ = [
    "ActionGroup",
    "DeviceAction",
    "DeviceActionLogger",
    "LogbookClient",
]
