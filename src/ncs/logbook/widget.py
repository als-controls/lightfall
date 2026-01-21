"""
Main logbook widget with dual markdown/WYSIWYG editing.

Provides a complete experiment logbook widget with:
- Raw markdown editing with syntax highlighting
- WYSIWYG rich text editing
- Protected content regions
- Mode switching with content synchronization
"""

from __future__ import annotations

from typing import Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
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

from ncs.logbook.converter import MarkdownConverter
from ncs.logbook.editors.markdown_editor import MarkdownEditor
from ncs.logbook.editors.richtext_editor import RichTextEditor
from ncs.logbook.protection import ProtectedRegion, ProtectionManager
from ncs.logbook.style import LogbookStyles


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

    The widget stores content as markdown internally and converts to/from
    HTML for the WYSIWYG view. Content is synchronized when switching modes.

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

        # Content changes
        self._rich_editor.content_changed.connect(self._on_rich_content_changed)
        self._markdown_editor.content_changed.connect(self._on_markdown_content_changed)

        # Protection violations - forward to our signal
        self._rich_editor.protection_violated.connect(self.protection_violated)
        self._markdown_editor.protection_violated.connect(self.protection_violated)

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

        Returns the markdown representation of the current content,
        converting from HTML if in WYSIWYG mode.

        Returns:
            The markdown content.
        """
        self._sync_from_current_mode()
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
        self._sync_from_current_mode()

        self._current_mode = mode

        if mode == "wysiwyg":
            self._stack.setCurrentIndex(0)
            self._rich_editor.set_markdown(self._markdown_content)
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

    def _sync_from_current_mode(self) -> None:
        """Sync markdown content from the current editor."""
        if self._syncing:
            return

        self._syncing = True
        try:
            if self._current_mode == "wysiwyg":
                self._markdown_content = self._rich_editor.get_markdown()
            else:
                self._markdown_content = self._markdown_editor.get_content()
        finally:
            self._syncing = False

    @Slot(int)
    def _on_mode_button_clicked(self, button_id: int) -> None:
        """Handle mode toggle button clicks."""
        mode = "wysiwyg" if button_id == 0 else "raw"
        self.set_mode(mode)

    @Slot()
    def _on_rich_content_changed(self) -> None:
        """Handle changes in WYSIWYG editor."""
        if not self._syncing:
            self.content_changed.emit()

    @Slot()
    def _on_markdown_content_changed(self) -> None:
        """Handle changes in raw markdown editor."""
        if self._syncing:
            return
        self._syncing = True
        try:
            # Re-parse protection regions (this triggers rehighlight via signal)
            content = self._markdown_editor.get_content()
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
