"""Logbook panel for the NCS application.

Provides a panel wrapping the LogbookWidget that displays the active
project's logbook and allows users to add notes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Signal, Slot
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

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Add note action
        self._add_note_action = QAction("Add Note", self)
        self._add_note_action.setToolTip("Add a new note entry (Ctrl+N)")
        self._add_note_action.setShortcut("Ctrl+N")
        self._add_note_action.triggered.connect(self._on_add_note)
        toolbar.addAction(self._add_note_action)

        toolbar.addSeparator()

        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.setToolTip("Refresh logbook content")
        refresh_action.triggered.connect(self._refresh_content)
        toolbar.addAction(refresh_action)

        layout.addWidget(toolbar)

        return header

    def _connect_service_signals(self) -> None:
        """Connect to ProjectService signals."""
        self._project_service.project_opened.connect(self._on_project_opened)
        self._project_service.project_closed.connect(self._on_project_closed)
        self._project_service.active_logbook_changed.connect(self._on_logbook_changed)
        self._project_service.entry_added.connect(self._on_entry_added)

    # === Content Management ===

    def _refresh_content(self) -> None:
        """Refresh the logbook content from the active project."""
        logbook = self._project_service.active_logbook

        if logbook is None:
            self._title_label.setText("No Logbook")
            self._logbook_widget.set_content("")
            self._add_note_action.setEnabled(False)
            return

        # Update title
        self._title_label.setText(logbook.title)
        self._add_note_action.setEnabled(True)

        # Render logbook to markdown and display
        markdown = logbook.to_markdown()
        self._logbook_widget.set_content(markdown)

        logger.debug("Refreshed logbook content: {}", logbook.title)

    def _scroll_to_bottom(self) -> None:
        """Scroll the logbook widget to the bottom."""
        # Access the internal editor and scroll
        if hasattr(self._logbook_widget, "_rich_editor"):
            editor = self._logbook_widget._rich_editor
            scrollbar = editor.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    # === Actions ===

    def _on_add_note(self) -> None:
        """Handle add note action."""
        if not self._project_service.has_project:
            QMessageBox.warning(
                self,
                "No Project",
                "Please open or create a project first.",
            )
            return

        # For now, use a simple input dialog
        # TODO: Replace with a proper note entry dialog
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Add Note",
            "Enter your note (markdown supported):",
        )

        if ok and text.strip():
            entry = self._project_service.add_note(text.strip())
            if entry:
                self.note_added.emit(text.strip())
                logger.info("Added note to logbook")

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
        self._refresh_content()
        self._scroll_to_bottom()

    @Slot(str, int)
    def _on_protection_violated(self, region_id: str, position: int) -> None:
        """Handle attempt to edit protected content."""
        logger.debug("Protection violation at region: {}", region_id)
        self.protection_violated.emit(region_id, position)

        # Show a brief status message
        # TODO: Use status bar or toast notification
        QMessageBox.information(
            self,
            "Protected Content",
            "This section is protected and cannot be edited.\n"
            "System-generated entries are read-only.",
        )

    @Slot()
    def _on_content_changed(self) -> None:
        """Handle content changed in the editor."""
        # Currently we don't sync edits back to the model
        # This would require parsing the markdown and identifying
        # which entries were modified. For now, notes are added
        # via the Add Note action.
        pass

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get logbook-specific introspection data."""
        logbook = self._project_service.active_logbook
        project = self._project_service.active_project

        return {
            "logbook": {
                "title": logbook.title if logbook else None,
                "entry_count": len(logbook.entries) if logbook else 0,
                "protected_regions": len(
                    self._logbook_widget.get_protected_regions()
                ),
            }
            if logbook
            else None,
            "project_name": project.name if project else None,
            "editor_mode": self._logbook_widget.get_mode(),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "add_note",
                "description": "Add a new note entry",
                "method": "action_add_note",
                "enabled": self._project_service.has_project,
            },
            {
                "name": "refresh",
                "description": "Refresh logbook content",
                "method": "action_refresh",
            },
            {
                "name": "switch_mode",
                "description": "Switch between visual and markdown mode",
                "method": "action_switch_mode",
                "parameters": {"mode": "string (raw or wysiwyg)"},
            },
        ])
        return actions

    def action_add_note(self, content: str | None = None) -> bool:
        """Action: Add a note to the logbook.

        Args:
            content: Note content. If None, shows dialog.

        Returns:
            True if note was added.
        """
        if content:
            entry = self._project_service.add_note(content)
            return entry is not None
        else:
            self._on_add_note()
            return True

    def action_refresh(self) -> bool:
        """Action: Refresh the logbook content."""
        self._refresh_content()
        return True

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
