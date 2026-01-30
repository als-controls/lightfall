"""Logbook panel for the LUCID application.

Provides a panel wrapping the LogbookWidget that displays the active
project's logbook and allows users to add notes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from PySide6.QtCore import QTimer, Signal, Slot
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

from lucid.logbook import DeviceActionLogger, LogbookWidget
from lucid.project import LogbookEntry, ProjectService
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.toast import ToastManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.acquire import QRunEngine
    from lucid.logbook.action_logger import ActionGroup


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
        id="lucid.panels.logbook",
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
        self._action_logger: DeviceActionLogger | None = None
        self._run_engine: QRunEngine | None = None
        self._current_run_uid: str | None = None
        self._current_run_start_doc: dict | None = None
        super().__init__(parent)

        # Connect to project service signals
        self._connect_service_signals()

        # Connect to device action logger
        self._connect_action_logger()

        # Connect to RunEngine
        self._connect_run_engine()

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
        from lucid.project.model import EntryType

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

        # Close any active action group since user added manual content
        # This ensures subsequent device actions create a new group
        # rather than merging into an old group separated by user content
        if self._action_logger and self._action_logger.has_active_group:
            self._action_logger.close_current_group()
            logger.debug("Closed action group due to user content edit")

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

    # === Device Action Logging ===

    def _connect_action_logger(self) -> None:
        """Connect to the DeviceActionLogger for automatic action recording."""
        self._action_logger = DeviceActionLogger.get_instance()
        self._action_logger.group_updated.connect(self._on_action_group_updated)
        self._action_logger.group_closed.connect(self._on_action_group_closed)
        logger.debug("Connected to DeviceActionLogger")

    @Slot(object)
    def _on_action_group_updated(self, group: ActionGroup) -> None:
        """Handle action group update (new action added to group).

        Args:
            group: The updated action group.
        """
        if not self._project_service.has_project:
            return

        region_id = f"action-{group.id}"

        # Check if this group already exists in the logbook
        existing_region = self._logbook_widget._protection_manager.get_region(region_id)

        if existing_region:
            # Safety check: Only update if the action group is still the last paragraph
            # (User edits should have already closed the group via _on_content_changed,
            # but this catches edge cases)
            if self._is_region_at_end(region_id):
                self._logbook_widget.update_action_group(region_id, group)
            else:
                # This shouldn't normally happen since we close groups on user edits,
                # but handle it gracefully by inserting as a new group
                logger.warning(
                    f"Action group {region_id} is not at end of content, "
                    "inserting as new group"
                )
                self._logbook_widget.insert_action_group(group)
                self._scroll_to_bottom()
        else:
            # Insert new group
            self._logbook_widget.insert_action_group(group)
            self._scroll_to_bottom()

        logger.debug(f"Action group {group.id} updated with {group.count} actions")

    def _is_region_at_end(self, region_id: str) -> bool:
        """Check if the given region is at the end of the content (no content after).

        Args:
            region_id: The region ID to check.

        Returns:
            True if there's no meaningful content after this region.
        """
        content = self._logbook_widget.get_content()
        if not content:
            return False

        region = self._logbook_widget._protection_manager.get_region(region_id)
        if region is None:
            return False

        # Check if there's any non-whitespace content after this region
        content_after = content[region.end_offset:].strip()

        # If there's content after (that's not just whitespace or placeholders),
        # then this region is not at the end
        if content_after and content_after != "\u00a0":
            return False

        return True

    @Slot(object)
    def _on_action_group_closed(self, group: ActionGroup) -> None:
        """Handle action group closed (finalized).

        Args:
            group: The closed action group.
        """
        if not self._project_service.has_project:
            return

        # Update the group one final time to ensure it's in sync
        region_id = f"action-{group.id}"
        existing_region = self._logbook_widget._protection_manager.get_region(region_id)

        if existing_region:
            self._logbook_widget.update_action_group(region_id, group)

        logger.debug(f"Action group {group.id} closed with {group.count} actions")

    def connect_control_widget(self, widget) -> None:
        """Connect a control widget to the action logger.

        This should be called when control widgets are created to enable
        automatic action logging.

        Args:
            widget: A BaseControlWidget instance.
        """
        if self._action_logger:
            self._action_logger.connect_to_control_widget(widget)
            logger.debug(f"Connected control widget {widget.__class__.__name__} to action logger")

    # === RunEngine Integration ===

    def _connect_run_engine(self) -> None:
        """Connect to the RunEngine for automatic run logging."""
        try:
            from lucid.acquire import get_run_engine

            self._run_engine = get_run_engine()
            self._run_engine.sigDocumentYield.connect(self._on_run_document)
            logger.debug("Connected to RunEngine for run logging")
        except Exception as e:
            logger.debug("Could not connect to RunEngine: {}", e)

    @Slot(str, dict)
    def _on_run_document(self, name: str, doc: dict) -> None:
        """Handle document from RunEngine.

        Inserts a run record into the current entry when a run starts.

        Args:
            name: Document type (start, descriptor, event, stop).
            doc: Document data.
        """
        if name == "start":
            self._on_run_start(doc)
        elif name == "stop":
            self._on_run_stop(doc)

    def _on_run_start(self, doc: dict) -> None:
        """Handle run start document - insert run record into current entry.

        Args:
            doc: Start document data.
        """
        if not self._project_service.has_project:
            return

        # Extract run information
        uid = doc.get("uid", "unknown")
        plan_name = doc.get("plan_name", "unknown")
        time_val = doc.get("time")

        # Format timestamp
        if time_val:
            from datetime import datetime
            ts = datetime.fromtimestamp(time_val)
            time_str = ts.strftime("%H:%M:%S")
        else:
            time_str = ""

        # Store run info for later update
        self._current_run_uid = uid
        self._current_run_start_doc = doc

        # Build compact format as protected markdown
        region_id = f"run-{uid[:8]}"

        # Use single-line format to avoid paragraph breaks inside the protected region
        content = f"**[Run]** {time_str} - {plan_name} (`{uid[:8]}`) *running...*"
        markdown = (
            f"<!-- PROTECTED:{region_id} -->"
            f"<!-- RUN:uid={uid}:plan={plan_name}:status=running -->"
            f"{content}"
            f"<!-- /PROTECTED:{region_id} -->"
        )

        # Append to current entry content via the logbook widget
        self._logbook_widget.append_content(markdown)
        self._scroll_to_bottom()

        logger.info(f"Inserted run record for {plan_name} ({uid[:8]})")

    def _on_run_stop(self, doc: dict) -> None:
        """Handle run stop document - update run record with result.

        Args:
            doc: Stop document data.
        """
        if not self._project_service.has_project:
            return

        run_uid = doc.get("run_start", "")

        # Only update if this matches our tracked run
        if run_uid != self._current_run_uid:
            return

        # Extract stop information
        exit_status = doc.get("exit_status", "unknown")
        reason = doc.get("reason", "")
        num_events = doc.get("num_events", {})
        time_val = doc.get("time")

        # Get start doc info
        start_doc = getattr(self, "_current_run_start_doc", {})
        plan_name = start_doc.get("plan_name", "unknown")
        start_time = start_doc.get("time")

        # Format timestamps
        if start_time:
            from datetime import datetime
            start_ts = datetime.fromtimestamp(start_time)
            start_str = start_ts.strftime("%H:%M:%S")
        else:
            start_str = ""

        # Calculate duration
        if start_time and time_val:
            duration = time_val - start_time
            if duration < 60:
                duration_str = f"{duration:.1f}s"
            else:
                mins = int(duration // 60)
                secs = duration % 60
                duration_str = f"{mins}m{secs:.0f}s"
        else:
            duration_str = ""

        # Format status indicator
        if exit_status == "success":
            status_str = "completed"
            total_events = sum(num_events.values()) if num_events else 0
            if total_events:
                status_str += f", {total_events} events"
        else:
            status_str = exit_status
            if reason:
                status_str += f": {reason}"

        # Build updated compact format
        region_id = f"run-{run_uid[:8]}"

        time_display = start_str
        if duration_str:
            time_display += f" ({duration_str})"

        # Use single-line format to avoid paragraph breaks inside the protected region
        content = f"**[Run]** {time_display} - {plan_name} (`{run_uid[:8]}`) *{status_str}*"
        new_markdown = (
            f"<!-- PROTECTED:{region_id} -->"
            f"<!-- RUN:uid={run_uid}:plan={plan_name}:status={exit_status} -->"
            f"{content}"
            f"<!-- /PROTECTED:{region_id} -->"
        )

        # Find and replace the existing run record
        region = self._logbook_widget._protection_manager.get_region(region_id)
        if region:
            content = self._logbook_widget.get_content()
            new_content = content[:region.start_offset] + new_markdown + content[region.end_offset:]
            self._logbook_widget.set_content(new_content)
            logger.info(f"Updated run record for {plan_name} ({run_uid[:8]}) - {exit_status}")
        else:
            logger.warning(f"Could not find run region {region_id} to update")

        # Clear tracking
        self._current_run_uid = None
        self._current_run_start_doc = None
