"""Sidebar panel for logbook entry navigation.

Displays the list of logbook entries in the left sidebar, communicating
with the main LogbookPanel via a shared signal bridge.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from lucid.logbook.entry_widget import EntryData, EntryListWidget
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.utils.logging import logger


class LogbookEntriesPanel(BasePanel):
    """Sidebar panel listing logbook entries.

    Layout::

        ┌──────────────┐
        │ ＋ New Entry  │
        │ Sort: Created │
        ├──────────────┤
        │ Entry 1      │
        │ Entry 2      │
        │ Entry 3      │
        │ ...          │
        └──────────────┘
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.logbook_entries",
        name="Entries",
        description="Logbook entry list for navigating experiment entries",
        icon="book-open",
        category="Core",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["logbook", "entries", "log", "navigate"],
        default_area="left",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=0,
    )

    # Forwarded signals from EntryListWidget
    entry_selected = Signal(str)
    entry_delete_requested = Signal(str)
    new_entry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def _setup_ui(self) -> None:
        self._entry_list = EntryListWidget()
        self._layout.addWidget(self._entry_list)

        # Forward signals
        self._entry_list.entry_selected.connect(self.entry_selected)
        self._entry_list.entry_delete_requested.connect(self.entry_delete_requested)
        self._entry_list.new_entry_requested.connect(self.new_entry_requested)

    # -- Public API (called by LogbookPanel) --

    @property
    def entry_list(self) -> EntryListWidget:
        """Direct access to the EntryListWidget."""
        return self._entry_list

    def set_entries(self, entries: list[EntryData]) -> None:
        """Replace the full entry list."""
        self._entry_list.set_entries(entries)

    def add_entry(self, entry: EntryData) -> None:
        """Add an entry to the list."""
        self._entry_list.add_entry(entry)

    def select_entry(self, entry_id: str) -> None:
        """Select an entry by ID."""
        self._entry_list.select_entry(entry_id)
