"""
Experiment logbook widget for NCS.

This module provides a markdown-based experiment logbook widget with:
- Dual editing modes: raw markdown and WYSIWYG
- Protected content regions using HTML comment markers
- Signal-based protection violation notification
- Theme-aware styling
- Automatic device action logging

Usage:
    >>> from lucid.logbook import LogbookWidget
    >>> logbook = LogbookWidget()
    >>> logbook.set_content("# My Experiment\\n\\nNotes...")
    >>> logbook.protection_violated.connect(handle_violation)
    >>> logbook.show()

Protected regions are defined using HTML comment syntax:
    <!-- PROTECTED:region-id -->
    Protected content here...
    <!-- /PROTECTED:region-id -->

Device action logging:
    >>> from lucid.logbook import DeviceActionLogger
    >>> logger = DeviceActionLogger.get_instance()
    >>> logger.connect_to_control_widget(motor_widget)
    >>> logger.action_recorded.connect(on_action)
"""

from lucid.logbook.action_dialog import ActionGroupDialog
from lucid.logbook.action_logger import ActionGroup, DeviceAction, DeviceActionLogger
from lucid.logbook.converter import MarkdownConverter
from lucid.logbook.protection import ActionGroupInfo, ProtectedRegion, ProtectionManager
from lucid.logbook.widget import LogbookWidget

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
