"""
Main logbook widget with dual markdown/WYSIWYG editing.

Provides a complete experiment logbook widget with:
- Raw markdown editing with syntax highlighting
- WYSIWYG rich text editing (markdown is source of truth)
- Protected content regions
- Mode switching with content synchronization
"""

from __future__ import annotations

# Import type hints only to avoid circular imports
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lucid.logbook.converter import MarkdownConverter
from lucid.logbook.editors.markdown_editor import MarkdownEditor
from lucid.logbook.editors.richtext_editor import RichTextEditor
from lucid.logbook.protection import ProtectedRegion, ProtectionManager
from lucid.logbook.style import LogbookStyles

if TYPE_CHECKING:
    from lucid.logbook.action_logger import ActionGroup


class LogbookWidget(QWidget):
    """
    Experiment logbook widget with dual markdown/WYSIWYG editing.

    This widget provides a complete editing experience for experiment
    logbooks with:
    - Raw markdown editing with syntax highlighting
    - WYSIWYG rich text editing
    - Protected content regions that emit signals on modification attempts
    - Theme-aware styling
    - Introspection API for Claude MCP tools

    **Markdown is the single source of truth.** In WYSIWYG mode, all edits
    are intercepted and translated to markdown operations. No HTML→MD
    conversion is needed.

    Attributes:
        widget_type: Type identifier for introspection.
        widget_description: Description for introspection.

    Signals:
        content_changed: Emitted when content is modified.
        protection_violated(str, int): Emitted when user tries to edit
            protected content. Args are (region_id, cursor_position).
        mode_changed(str): Emitted when switching modes ("raw" or "wysiwyg").

    Example:
        >>> logbook = LogbookWidget()
        >>> logbook.set_content("# Experiment Log\\n\\nNotes...")
        >>> logbook.protection_violated.connect(handle_violation)
        >>> logbook.show()
    """

    widget_type: ClassVar[str] = "LogbookWidget"
    widget_description: ClassVar[str] = (
        "Markdown-based experiment logbook with dual editing modes"
    )

    # Signals
    content_changed = Signal()
    protection_violated = Signal(str, int)  # (region_id, cursor_position)
    mode_changed = Signal(str)  # "raw" or "wysiwyg"

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Initialize the logbook widget.

        Args:
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)

        # Core components
        self._protection_manager = ProtectionManager(self)
        self._converter = MarkdownConverter()

        # State
        self._current_mode = "wysiwyg"
        self._markdown_content = ""
        self._syncing = False

        self._setup_ui()
        self._connect_signals()

        # Set object name for identification
        self.setObjectName("LogbookWidget")

    def _setup_ui(self) -> None:
        """Create the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        # Stacked widget for editor modes
        self._stack = QStackedWidget()

        # WYSIWYG editor (index 0)
        self._rich_editor = RichTextEditor(
            self._protection_manager,
            self._converter,
            self,
        )
        self._rich_editor.setObjectName("LogbookRichEditor")
        self._stack.addWidget(self._rich_editor)

        # Raw markdown editor (index 1)
        self._markdown_editor = MarkdownEditor(
            self._protection_manager,
            self,
        )
        self._markdown_editor.setObjectName("LogbookMarkdownEditor")
        self._stack.addWidget(self._markdown_editor)

        layout.addWidget(self._stack)

    def _create_toolbar(self) -> QToolBar:
        """Create the editing toolbar."""
        toolbar = QToolBar()
        toolbar.setObjectName("LogbookToolbar")
        toolbar.setMovable(False)
        toolbar.setStyleSheet(LogbookStyles.toolbar())

        # Mode toggle buttons
        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(4, 0, 4, 0)
        mode_layout.setSpacing(2)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        self._wysiwyg_btn = QToolButton()
        self._wysiwyg_btn.setText("Visual")
        self._wysiwyg_btn.setCheckable(True)
        self._wysiwyg_btn.setChecked(True)
        self._wysiwyg_btn.setToolTip("WYSIWYG editing mode")
        self._mode_group.addButton(self._wysiwyg_btn, 0)
        toolbar.addWidget(self._wysiwyg_btn)

        self._raw_btn = QToolButton()
        self._raw_btn.setText("Markdown")
        self._raw_btn.setCheckable(True)
        self._raw_btn.setToolTip("Raw markdown editing mode")
        self._mode_group.addButton(self._raw_btn, 1)
        toolbar.addWidget(self._raw_btn)

        toolbar.addSeparator()

        # Formatting actions (for WYSIWYG mode)
        self._bold_action = QAction("B", self)
        self._bold_action.setToolTip("Bold (Ctrl+B)")
        self._bold_action.setShortcut(QKeySequence.StandardKey.Bold)
        self._bold_action.setCheckable(True)
        font = self._bold_action.font()
        font.setBold(True)
        self._bold_action.setFont(font)
        self._bold_action.triggered.connect(self._toggle_bold)
        toolbar.addAction(self._bold_action)

        self._italic_action = QAction("I", self)
        self._italic_action.setToolTip("Italic (Ctrl+I)")
        self._italic_action.setShortcut(QKeySequence.StandardKey.Italic)
        self._italic_action.setCheckable(True)
        font = self._italic_action.font()
        font.setItalic(True)
        self._italic_action.setFont(font)
        self._italic_action.triggered.connect(self._toggle_italic)
        toolbar.addAction(self._italic_action)

        self._strike_action = QAction("S", self)
        self._strike_action.setToolTip("Strikethrough")
        self._strike_action.setCheckable(True)
        font = self._strike_action.font()
        font.setStrikeOut(True)
        self._strike_action.setFont(font)
        self._strike_action.triggered.connect(self._toggle_strikethrough)
        toolbar.addAction(self._strike_action)

        toolbar.addSeparator()

        # Heading actions
        self._h1_action = QAction("H1", self)
        self._h1_action.setToolTip("Heading 1")
        self._h1_action.triggered.connect(lambda: self._set_heading(1))
        toolbar.addAction(self._h1_action)

        self._h2_action = QAction("H2", self)
        self._h2_action.setToolTip("Heading 2")
        self._h2_action.triggered.connect(lambda: self._set_heading(2))
        toolbar.addAction(self._h2_action)

        self._h3_action = QAction("H3", self)
        self._h3_action.setToolTip("Heading 3")
        self._h3_action.triggered.connect(lambda: self._set_heading(3))
        toolbar.addAction(self._h3_action)

        self._normal_action = QAction("Normal", self)
        self._normal_action.setToolTip("Normal text")
        self._normal_action.triggered.connect(lambda: self._set_heading(0))
        toolbar.addAction(self._normal_action)

        # Store formatting actions for enabling/disabling
        self._formatting_actions = [
            self._bold_action,
            self._italic_action,
            self._strike_action,
            self._h1_action,
            self._h2_action,
            self._h3_action,
            self._normal_action,
        ]

        return toolbar

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        # Mode switching
        self._mode_group.idClicked.connect(self._on_mode_button_clicked)

        # Content changes - markdown editor notifies directly
        self._markdown_editor.content_changed.connect(self._on_markdown_content_changed)

        # Rich editor edits go through markdown_edit_requested
        self._rich_editor.markdown_edit_requested.connect(self._on_markdown_edit_requested)
        self._rich_editor.content_changed.connect(self._on_rich_content_changed)

        # Protection violations - forward to our signal
        self._rich_editor.protection_violated.connect(self.protection_violated)
        self._markdown_editor.protection_violated.connect(self.protection_violated)

        # Action group clicks - show details dialog
        self._rich_editor.action_group_clicked.connect(self.show_action_group_details)

        # Track cursor position to update formatting button states
        self._rich_editor.cursorPositionChanged.connect(self._update_formatting_button_states)
        self._rich_editor.selectionChanged.connect(self._update_formatting_button_states)

    # === Public API ===

    def set_content(self, markdown: str) -> None:
        """
        Set the logbook content from markdown.

        This method loads the markdown content, parses protected regions,
        and updates the current editor view.

        Args:
            markdown: The markdown content to display/edit.
        """
        self._syncing = True
        try:
            self._markdown_content = markdown

            if self._current_mode == "wysiwyg":
                self._rich_editor.set_markdown(markdown)
            else:
                self._markdown_editor.set_content(markdown)

            # Parse regions after content is loaded (set_markdown also parses,
            # but we do it here to ensure regions are set for both modes)
            self._protection_manager.parse_regions(markdown)
        finally:
            self._syncing = False

        logger.debug(f"Loaded content ({len(markdown)} chars)")

    def get_content(self) -> str:
        """
        Get the current content as markdown.

        Returns the markdown source of truth. In WYSIWYG mode, the markdown
        is kept synchronized by the editor's edit interception mechanism.

        Returns:
            The markdown content.
        """
        # In markdown mode, sync from the editor
        if self._current_mode == "raw":
            self._markdown_content = self._markdown_editor.get_content()
        # In WYSIWYG mode, _markdown_content is already up to date
        # (updated via markdown_edit_requested signal)
        return self._markdown_content

    def set_mode(self, mode: str) -> None:
        """
        Set the editing mode.

        Switches between raw markdown and WYSIWYG editing modes,
        synchronizing content in the process.

        Args:
            mode: Either "raw" or "wysiwyg".

        Raises:
            ValueError: If mode is not "raw" or "wysiwyg".
        """
        if mode not in ("raw", "wysiwyg"):
            raise ValueError(f"Invalid mode: {mode!r}. Must be 'raw' or 'wysiwyg'.")

        if mode == self._current_mode:
            return

        # Sync content before switching
        # In raw mode, get content from markdown editor
        if self._current_mode == "raw":
            self._markdown_content = self._markdown_editor.get_content()
        # In WYSIWYG mode, _markdown_content is already up to date

        self._current_mode = mode

        if mode == "wysiwyg":
            self._stack.setCurrentIndex(0)
            self._rich_editor.render_markdown(self._markdown_content)
            self._wysiwyg_btn.setChecked(True)
            self._set_formatting_enabled(True)
        else:
            self._stack.setCurrentIndex(1)
            self._markdown_editor.set_content(self._markdown_content)
            self._raw_btn.setChecked(True)
            self._set_formatting_enabled(False)

        logger.debug(f"Switched to {mode} mode")
        self.mode_changed.emit(mode)

    def get_mode(self) -> str:
        """
        Get the current editing mode.

        Returns:
            Either "raw" or "wysiwyg".
        """
        return self._current_mode

    def unlock_region(self, region_id: str) -> bool:
        """
        Temporarily unlock a protected region for editing.

        Args:
            region_id: The ID of the region to unlock.

        Returns:
            True if successfully unlocked, False if region not found.
        """
        return self._protection_manager.unlock_region(region_id)

    def lock_region(self, region_id: str) -> bool:
        """
        Re-lock a previously unlocked region.

        Args:
            region_id: The ID of the region to lock.

        Returns:
            True if successfully locked, False if region not found.
        """
        return self._protection_manager.lock_region(region_id)

    def lock_all_regions(self) -> None:
        """Lock all currently unlocked regions."""
        self._protection_manager.lock_all_regions()

    def get_protected_regions(self) -> list[ProtectedRegion]:
        """
        Get all protected regions in the document.

        Returns:
            List of ProtectedRegion objects.
        """
        return self._protection_manager.get_regions()

    def is_region_unlocked(self, region_id: str) -> bool:
        """
        Check if a region is currently unlocked.

        Args:
            region_id: The ID of the region to check.

        Returns:
            True if the region exists and is unlocked.
        """
        region = self._protection_manager.get_region(region_id)
        return region is not None and region.unlocked

    # === Introspection API ===

    def get_introspection_data(self) -> dict[str, Any]:
        """
        Get comprehensive introspection data for Claude MCP tools.

        Returns:
            Dictionary with widget information including:
            - widget_type and description
            - current mode and content info
            - protected region details
            - widget state
        """
        return {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "object_name": self.objectName(),
            "class_name": self.__class__.__name__,
            "current_mode": self._current_mode,
            "content_length": len(self._markdown_content),
            "protected_regions": [
                {
                    "region_id": r.region_id,
                    "start": r.start_offset,
                    "end": r.end_offset,
                    "content_length": len(r.content),
                    "unlocked": r.unlocked,
                }
                for r in self._protection_manager.get_regions()
            ],
            "enabled": self.isEnabled(),
            "visible": self.isVisible(),
            "geometry": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
            },
        }

    @classmethod
    def get_class_introspection_data(cls) -> dict[str, Any]:
        """
        Get class-level introspection data.

        Returns:
            Dictionary with class information.
        """
        return {
            "widget_type": cls.widget_type,
            "widget_description": cls.widget_description,
            "class_name": cls.__name__,
            "module": cls.__module__,
            "supported_modes": ["raw", "wysiwyg"],
            "protection_syntax": "<!-- PROTECTED:id -->...<!-- /PROTECTED:id -->",
        }

    # === Internal Methods ===

    @Slot(int)
    def _on_mode_button_clicked(self, button_id: int) -> None:
        """Handle mode toggle button clicks."""
        mode = "wysiwyg" if button_id == 0 else "raw"
        self.set_mode(mode)

    @Slot(str)
    def _on_markdown_edit_requested(self, new_markdown: str) -> None:
        """Handle markdown edit from WYSIWYG editor.

        This is called when the rich editor intercepts an edit and
        translates it to a markdown operation.
        """
        if self._syncing:
            return
        self._markdown_content = new_markdown
        # Note: content_changed is emitted by _on_rich_content_changed

    @Slot()
    def _on_rich_content_changed(self) -> None:
        """Handle changes in WYSIWYG editor."""
        if not self._syncing:
            self.content_changed.emit()

    @Slot()
    def _on_markdown_content_changed(self) -> None:
        """Handle changes in raw markdown editor."""
        # Only process when we're actually in raw mode
        if self._current_mode != "raw":
            return
        if self._syncing:
            return
        self._syncing = True
        try:
            # Update markdown content and re-parse protection regions
            content = self._markdown_editor.get_content()
            self._markdown_content = content
            self._protection_manager.parse_regions(content)
            self.content_changed.emit()
        finally:
            self._syncing = False

    def _set_formatting_enabled(self, enabled: bool) -> None:
        """Enable or disable formatting actions."""
        for action in self._formatting_actions:
            action.setEnabled(enabled)

    @Slot()
    def _update_formatting_button_states(self) -> None:
        """Update formatting button checked states based on cursor position."""
        if self._current_mode != "wysiwyg":
            return

        cursor = self._rich_editor.textCursor()
        char_format = cursor.charFormat()

        # Block signals to avoid triggering formatting changes
        self._bold_action.blockSignals(True)
        self._italic_action.blockSignals(True)
        self._strike_action.blockSignals(True)

        # Update button states to reflect current format
        self._bold_action.setChecked(
            char_format.fontWeight() == QFont.Weight.Bold
        )
        self._italic_action.setChecked(char_format.fontItalic())
        self._strike_action.setChecked(char_format.fontStrikeOut())

        self._bold_action.blockSignals(False)
        self._italic_action.blockSignals(False)
        self._strike_action.blockSignals(False)

    def _toggle_bold(self) -> None:
        """Apply bold formatting based on button state."""
        if self._current_mode == "wysiwyg":
            # Set the format to match the button's new checked state
            self._rich_editor.set_bold(self._bold_action.isChecked())

    def _toggle_italic(self) -> None:
        """Apply italic formatting based on button state."""
        if self._current_mode == "wysiwyg":
            self._rich_editor.set_italic(self._italic_action.isChecked())

    def _toggle_strikethrough(self) -> None:
        """Apply strikethrough formatting based on button state."""
        if self._current_mode == "wysiwyg":
            self._rich_editor.set_strikethrough(self._strike_action.isChecked())

    def _set_heading(self, level: int) -> None:
        """Set heading level."""
        if self._current_mode == "wysiwyg":
            self._rich_editor.set_heading(level)

    # === Action Logging Methods ===

    def insert_action_group(self, action_group: ActionGroup) -> None:
        """
        Insert an action group into the logbook.

        The action group is formatted as protected markdown and appended
        to the current content.

        Args:
            action_group: The ActionGroup to insert.
        """
        from lucid.logbook.action_logger import DeviceActionLogger

        logger_instance = DeviceActionLogger.get_instance()
        markdown = logger_instance.format_group_markdown(action_group)

        self.append_content(markdown)
        logger.debug(f"Inserted action group {action_group.id} with {action_group.count} actions")

    def append_content(self, markdown: str) -> None:
        """
        Append markdown content to the end of the logbook.

        Args:
            markdown: The markdown content to append.
        """
        current = self.get_content()
        # Ensure there's a newline separator
        if current and not current.endswith("\n"):
            current += "\n"
        if current and not current.endswith("\n\n"):
            current += "\n"

        new_content = current + markdown
        self.set_content(new_content)

    def update_action_group(self, region_id: str, action_group: ActionGroup) -> bool:
        """
        Update an existing action group entry in the logbook.

        Replaces the content of the protected region with the new action
        group content.

        Args:
            region_id: The ID of the action group region to update.
            action_group: The updated ActionGroup.

        Returns:
            True if the region was found and updated, False otherwise.
        """
        from lucid.logbook.action_logger import DeviceActionLogger

        region = self._protection_manager.get_region(region_id)
        if region is None:
            logger.warning(f"Action group region not found: {region_id}")
            return False

        # Format the new content
        logger_instance = DeviceActionLogger.get_instance()
        new_markdown = logger_instance.format_group_markdown(action_group)

        # Replace the old region with new content
        content = self.get_content()
        new_content = content[:region.start_offset] + new_markdown + content[region.end_offset:]

        self.set_content(new_content)
        logger.debug(f"Updated action group {region_id} with {action_group.count} actions")
        return True

    def show_action_group_details(self, region_id: str) -> None:
        """
        Show a dialog with action group details.

        Args:
            region_id: The ID of the action group region.
        """
        from lucid.logbook.action_dialog import ActionGroupDialog
        from lucid.logbook.action_logger import DeviceActionLogger

        # Get the action group info
        info = self._protection_manager.get_action_group_info(region_id)
        if info is None:
            logger.warning(f"Action group info not found: {region_id}")
            return

        # Get the actions from the DeviceActionLogger if it's the current group
        logger_instance = DeviceActionLogger.get_instance()
        if logger_instance.current_group and logger_instance.current_group.id == region_id.replace("action-", ""):
            actions = logger_instance.current_group.actions
        else:
            # TODO: Parse actions from markdown content for historical groups
            actions = []
            logger.debug(f"Historical action group {region_id} - parsing not yet implemented")

        # Show the dialog
        dialog = ActionGroupDialog(region_id, actions, self)
        dialog.exec()

    def get_last_action_group_id(self) -> str | None:
        """
        Get the ID of the most recent action group in the logbook.

        Returns:
            The region ID of the last action group, or None if there are none.
        """
        groups = self._protection_manager.get_action_groups()
        if not groups:
            return None
        # Return the last one (highest offset)
        last = max(groups, key=lambda g: g[0].start_offset)
        return last[0].region_id

    def notify_user_edit(self) -> None:
        """
        Notify that the user has made a manual edit.

        This closes any active action group to prevent it from being
        extended with future device actions.
        """
        from lucid.logbook.action_logger import DeviceActionLogger

        logger_instance = DeviceActionLogger.get_instance()
        logger_instance.close_current_group()
