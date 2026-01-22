"""Logbook panel for the NCS application.

Provides a panel wrapping the LogbookWidget that displays the active
project's logbook and allows users to add notes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from uuid import UUID

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ncs.ui.toast import ToastManager

from ncs.logbook import LogbookWidget
from ncs.project import Logbook, LogbookEntry, ProjectService
from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.utils.logging import logger

if TYPE_CHECKING:
    pass


class LogbookPanel(BasePanel):
    """Panel for displaying and editing the active logbook.

    LogbookPanel is a default panel that:
    - Displays the active project's active logbook
    - Allows users to add notes
    - Shows system-generated entries (protected)
    - Updates when the active logbook changes

    This panel is designed to be always visible and provides the
    primary interface for experiment documentation.

    Signals:
        note_added: Emitted when user adds a note.
        protection_violated: Emitted when user tries to edit protected content.
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="ncs.panels.logbook",
        name="Logbook",
        description="Experiment logbook for recording notes and viewing system events",
        icon="logbook",
        category="Core",
        required_permission=None,  # Everyone can view the logbook
        singleton=True,
        closable=False,  # Always visible
        keywords=["log", "notes", "experiment", "journal", "record"],
    )

    # Signals
    note_added = Signal(str)  # note content
    protection_violated = Signal(str, int)  # region_id, position

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the logbook panel.

        Args:
            parent: Parent widget.
        """
        self._project_service = ProjectService.get_instance()
        self._current_entry_id: UUID | None = None
        self._sync_timer: QTimer | None = None
        super().__init__(parent)

        # Connect to project service signals
        self._connect_service_signals()

        # Load initial content
        self._refresh_content()

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Header with logbook info and toolbar
        header = self._create_header()
        self._layout.addWidget(header)

        # Logbook widget
        self._logbook_widget = LogbookWidget(self)
        self._logbook_widget.protection_violated.connect(self._on_protection_violated)
        self._logbook_widget.content_changed.connect(self._on_content_changed)
        self._layout.addWidget(self._logbook_widget)

    def _create_header(self) -> QWidget:
        """Create the header with logbook info and toolbar."""
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top row: logbook title and selector
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._title_label = QLabel("No Logbook")
        self._title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        top_row.addWidget(self._title_label)

        top_row.addStretch()

        # Logbook selector button (for switching between logbooks)
        self._logbook_btn = QToolButton()
        self._logbook_btn.setText("Switch Logbook")
        self._logbook_btn.setToolTip("Switch to a different logbook")
        self._logbook_btn.clicked.connect(self._on_switch_logbook)
        top_row.addWidget(self._logbook_btn)

        layout.addLayout(top_row)

        # Entry info row
        entry_row = QHBoxLayout()
        entry_row.setSpacing(8)

        self._entry_type_label = QLabel()
        self._entry_type_label.setStyleSheet("color: gray;")
        entry_row.addWidget(self._entry_type_label)

        self._entry_title_label = QLabel()
        self._entry_title_label.setStyleSheet("font-style: italic;")
        entry_row.addWidget(self._entry_title_label)

        entry_row.addStretch()

        self._entry_timestamp_label = QLabel()
        self._entry_timestamp_label.setStyleSheet("color: gray; font-size: 12px;")
        entry_row.addWidget(self._entry_timestamp_label)

        layout.addLayout(entry_row)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # New entry action
        self._new_entry_action = QAction("New Entry", self)
        self._new_entry_action.setToolTip("Create a new note entry (Ctrl+N)")
        self._new_entry_action.setShortcut("Ctrl+N")
        self._new_entry_action.triggered.connect(self._on_new_entry)
        toolbar.addAction(self._new_entry_action)

        layout.addWidget(toolbar)

        return header

    def _connect_service_signals(self) -> None:
        """Connect to ProjectService signals."""
        self._project_service.project_opened.connect(self._on_project_opened)
        self._project_service.project_closed.connect(self._on_project_closed)
        self._project_service.active_logbook_changed.connect(self._on_logbook_changed)
        self._project_service.active_entry_changed.connect(self._on_entry_changed)
        self._project_service.entry_added.connect(self._on_entry_added)

    # === Content Management ===

    def _refresh_content(self) -> None:
        """Refresh to show the active entry."""
        entry = self._project_service.active_entry
        logbook = self._project_service.active_logbook

        if logbook is None:
            self._title_label.setText("No Logbook")
            self._show_empty_state("No project open")
            return

        self._title_label.setText(logbook.title)
        self._new_entry_action.setEnabled(True)

        if entry is None:
            self._show_empty_state("No entries. Click 'New Entry' to create one.")
            return

        self._show_entry(entry)

    def _show_entry(self, entry: LogbookEntry) -> None:
        """Display a single entry.

        Args:
            entry: The entry to display.
        """
        from ncs.project.model import EntryType

        # Update entry metadata display
        type_labels = {
            EntryType.NOTE: "",
            EntryType.ACTION: "[Action]",
            EntryType.SCAN: "[Scan]",
            EntryType.SNAPSHOT: "[Snapshot]",
            EntryType.SYSTEM: "[System]",
        }
        self._entry_type_label.setText(type_labels.get(entry.entry_type, ""))
        self._entry_title_label.setText(entry.get_title())
        self._entry_timestamp_label.setText(
            entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        )

        # Show content - just raw content for editing, not wrapped in markdown header
        self._logbook_widget.set_content(entry.content)

        # Set read-only if protected
        self._logbook_widget.setEnabled(not entry.protected)

        # Track current entry for content sync
        self._current_entry_id = entry.id

        logger.debug("Showing entry: {}", entry.get_title())

    def _show_empty_state(self, message: str) -> None:
        """Show empty state placeholder.

        Args:
            message: Message to display.
        """
        self._entry_type_label.setText("")
        self._entry_title_label.setText("")
        self._entry_timestamp_label.setText("")
        self._logbook_widget.set_content(f"*{message}*")
        self._logbook_widget.setEnabled(False)
        self._current_entry_id = None
        self._new_entry_action.setEnabled(self._project_service.has_project)

    def _scroll_to_bottom(self) -> None:
        """Scroll the logbook widget to the bottom."""
        # Access the internal editor and scroll
        if hasattr(self._logbook_widget, "_rich_editor"):
            editor = self._logbook_widget._rich_editor
            scrollbar = editor.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    # === Actions ===

    def _on_new_entry(self) -> None:
        """Handle new entry action."""
        if not self._project_service.has_project:
            QMessageBox.warning(
                self,
                "No Project",
                "Please open or create a project first.",
            )
            return

        # Create empty entry - user edits directly in the editor
        entry = self._project_service.create_note_entry()
        if entry:
            logger.info("Created new entry")

    def _on_switch_logbook(self) -> None:
        """Handle switch logbook action."""
        project = self._project_service.active_project
        if project is None or len(project.logbooks) <= 1:
            return

        # Get logbook names
        logbook_names = [lb.title for lb in project.logbooks]
        current_idx = 0
        if project.active_logbook:
            try:
                current_idx = [lb.id for lb in project.logbooks].index(
                    project.active_logbook.id
                )
            except ValueError:
                pass

        name, ok = QInputDialog.getItem(
            self,
            "Switch Logbook",
            "Select logbook:",
            logbook_names,
            current_idx,
            False,
        )

        if ok and name:
            # Find and switch to selected logbook
            for logbook in project.logbooks:
                if logbook.title == name:
                    self._project_service.set_active_logbook(logbook.id)
                    break

    # === Signal Handlers ===

    @Slot(object)
    def _on_project_opened(self, project) -> None:
        """Handle project opened."""
        self._refresh_content()

    @Slot()
    def _on_project_closed(self) -> None:
        """Handle project closed."""
        self._refresh_content()

    @Slot(object)
    def _on_logbook_changed(self, logbook) -> None:
        """Handle active logbook changed."""
        self._refresh_content()

    @Slot(object)
    def _on_entry_added(self, entry: LogbookEntry) -> None:
        """Handle new entry added to logbook."""
        # Entry should already be active, just refresh
        self._refresh_content()

    @Slot(object)
    def _on_entry_changed(self, entry: LogbookEntry | None) -> None:
        """Handle active entry changed."""
        self._refresh_content()

    @Slot(str, int)
    def _on_protection_violated(self, region_id: str, position: int) -> None:
        """Handle attempt to edit protected content."""
        logger.debug("Protection violation at region: {}", region_id)
        self.protection_violated.emit(region_id, position)

        ToastManager.get_instance().warning(
            "Protected Content",
            "System-generated entries are read-only.",
        )

    @Slot()
    def _on_content_changed(self) -> None:
        """Handle content changed in the editor."""
        if self._current_entry_id is None:
            return

        # Debounce: Only sync after typing stops
        if self._sync_timer is None:
            self._sync_timer = QTimer(self)
            self._sync_timer.setSingleShot(True)
            self._sync_timer.timeout.connect(self._sync_content_to_model)

        self._sync_timer.start(500)  # 500ms debounce

    def _sync_content_to_model(self) -> None:
        """Sync editor content back to the entry model."""
        if self._current_entry_id is None:
            return

        content = self._logbook_widget.get_content()
        if self._project_service.update_entry_content(self._current_entry_id, content):
            # Update the title label since it may have changed
            entry = self._project_service.active_entry
            if entry:
                self._entry_title_label.setText(entry.get_title())

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get logbook-specific introspection data."""
        logbook = self._project_service.active_logbook
        project = self._project_service.active_project
        entry = self._project_service.active_entry

        return {
            "logbook": {
                "title": logbook.title if logbook else None,
                "entry_count": len(logbook.entries) if logbook else 0,
            }
            if logbook
            else None,
            "active_entry": {
                "id": str(entry.id),
                "title": entry.get_title(),
                "type": entry.entry_type.value,
                "protected": entry.protected,
            }
            if entry
            else None,
            "project_name": project.name if project else None,
            "editor_mode": self._logbook_widget.get_mode(),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "new_entry",
                "description": "Create a new note entry",
                "method": "action_new_entry",
                "enabled": self._project_service.has_project,
            },
            {
                "name": "switch_mode",
                "description": "Switch between visual and markdown mode",
                "method": "action_switch_mode",
                "parameters": {"mode": "string (raw or wysiwyg)"},
            },
        ])
        return actions

    def action_new_entry(self) -> bool:
        """Action: Create a new note entry.

        Returns:
            True if entry was created.
        """
        entry = self._project_service.create_note_entry()
        return entry is not None

    def action_switch_mode(self, mode: str = "wysiwyg") -> bool:
        """Action: Switch editor mode.

        Args:
            mode: 'raw' or 'wysiwyg'

        Returns:
            True if mode was changed.
        """
        try:
            self._logbook_widget.set_mode(mode)
            return True
        except ValueError:
            return False
