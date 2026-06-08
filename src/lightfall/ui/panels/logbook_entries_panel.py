"""Sidebar panel for logbook entry navigation.

Displays the list of logbook entries in the left sidebar, communicating
with the main LogbookPanel via a shared signal bridge.
"""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from lightfall.logbook.entry_widget import EntryData, EntryListWidget
from lightfall.ui.panels.base import BasePanel, PanelMetadata


class LogbookEntriesPanel(BasePanel):
    """Sidebar panel listing logbook entries.

    The New Entry button and the Sort control live in the panel title bar
    (added in :meth:`_setup_ui`); the entry list itself fills the body.

    Layout::

        ┌─────────── ＋ ⇅ ┐   (title bar: New Entry, Sort)
        │ Entry 1        │
        │ Entry 2        │
        │ Entry 3        │
        │ ...            │
        └────────────────┘
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.logbook_entries",
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
        # The New Entry button and sort control live in the panel title bar
        # (see below), so omit the in-widget toolbar.
        self._entry_list = EntryListWidget(show_toolbar=False)
        self._layout.addWidget(self._entry_list)

        # Forward signals
        self._entry_list.entry_selected.connect(self.entry_selected)
        self._entry_list.entry_delete_requested.connect(self.entry_delete_requested)
        self._entry_list.new_entry_requested.connect(self.new_entry_requested)

        # Title bar: New Entry button drives the same new_entry_requested signal.
        self.add_title_bar_button(
            "mdi6.plus", "New Entry", self._entry_list.new_entry_requested
        )

        # Title bar: Sort menu applies the same sort keys as the old combo.
        sort_menu = QMenu()
        for label, key in EntryListWidget.SORT_OPTIONS:
            act = QAction(label, sort_menu)
            act.triggered.connect(lambda _checked=False, k=key: self._entry_list.set_sort_key(k))
            sort_menu.addAction(act)
        self.add_title_bar_button("mdi6.sort", "Sort", menu=sort_menu)

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
