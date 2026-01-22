"""
Experiment logbook widget for NCS.

This module provides a markdown-based experiment logbook widget with:
- Dual editing modes: raw markdown and WYSIWYG
- Protected content regions using HTML comment markers
- Signal-based protection violation notification
- Theme-aware styling
- Automatic device action logging

Usage:
    >>> from ncs.logbook import LogbookWidget
    >>> logbook = LogbookWidget()
    >>> logbook.set_content("# My Experiment\\n\\nNotes...")
    >>> logbook.protection_violated.connect(handle_violation)
    >>> logbook.show()

Protected regions are defined using HTML comment syntax:
    <!-- PROTECTED:region-id -->
    Protected content here...
    <!-- /PROTECTED:region-id -->

Device action logging:
    >>> from ncs.logbook import DeviceActionLogger
    >>> logger = DeviceActionLogger.get_instance()
    >>> logger.connect_to_control_widget(motor_widget)
    >>> logger.action_recorded.connect(on_action)
"""

from ncs.logbook.action_dialog import ActionGroupDialog
from ncs.logbook.action_logger import ActionGroup, DeviceAction, DeviceActionLogger
from ncs.logbook.converter import MarkdownConverter
from ncs.logbook.protection import ActionGroupInfo, ProtectedRegion, ProtectionManager
from ncs.logbook.widget import LogbookWidget

__all__ = [
    "ActionGroup",
    "ActionGroupDialog",
    "ActionGroupInfo",
    "DeviceAction",
    "DeviceActionLogger",
    "LogbookWidget",
    "MarkdownConverter",
    "ProtectedRegion",
    "ProtectionManager",
]
