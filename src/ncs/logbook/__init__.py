"""
Experiment logbook widget for NCS.

This module provides a markdown-based experiment logbook widget with:
- Dual editing modes: raw markdown and WYSIWYG
- Protected content regions using HTML comment markers
- Signal-based protection violation notification
- Theme-aware styling

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
"""

from ncs.logbook.converter import MarkdownConverter
from ncs.logbook.protection import ProtectedRegion, ProtectionManager
from ncs.logbook.widget import LogbookWidget

__all__ = [
    "LogbookWidget",
    "MarkdownConverter",
    "ProtectedRegion",
    "ProtectionManager",
]
