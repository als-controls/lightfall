"""
Raw markdown editor with protection enforcement.

Provides a QPlainTextEdit-based editor with syntax highlighting and
protection for designated content regions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ncs.logbook.editors.highlighter import ProtectedMarkdownHighlighter
from ncs.logbook.style import LogbookStyles

if TYPE_CHECKING:
    from ncs.logbook.protection import ProtectionManager


class MarkdownEditor(QPlainTextEdit):
    """
    Raw markdown text editor with protection enforcement.

    This editor provides:
    - Syntax highlighting for markdown
    - Visual highlighting of protected regions
    - Edit interception for protected content
    - Signal emission when protection is violated

    The editor intercepts key events and other edit operations to check
    whether they would modify protected content. If so, it emits the
    `protection_violated` signal and blocks the edit.

    Attributes:
        widget_type: Type identifier for introspection.
        widget_description: Description for introspection.

    Signals:
        content_changed: Emitted when the content is modified.
        protection_violated(str, int): Emitted when an edit is attempted
            in a protected region. Args are (region_id, cursor_position).

    Example:
        >>> editor = MarkdownEditor(protection_manager)
        >>> editor.setPlainText("# Hello\\n\\nWorld")
        >>> editor.protection_violated.connect(lambda r, p: print(f"Blocked: {r}"))
    """

    widget_type: ClassVar[str] = "MarkdownEditor"
    widget_description: ClassVar[str] = "Raw markdown text editor with protection"

    # Signals
    content_changed = Signal()
    protection_violated = Signal(str, int)  # (region_id, cursor_position)

    def __init__(
        self,
        protection_manager: ProtectionManager,
        parent: QWidget | None = None,
    ) -> None:
        """
        Initialize the markdown editor.

        Args:
            protection_manager: Manager for protected content regions.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._protection_manager = protection_manager

        self._setup_ui()
        self._setup_highlighter()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Configure the editor UI."""
        # Set monospace font
        self.setStyleSheet(LogbookStyles.editor_base())

        # Configure editor behavior
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabStopDistance(
            self.fontMetrics().horizontalAdvance(" ") * 4
        )

        # Set placeholder text
        self.setPlaceholderText("Enter markdown content...")

    def _setup_highlighter(self) -> None:
        """Set up syntax highlighting."""
        self._highlighter = ProtectedMarkdownHighlighter(
            self.document(),
            self._protection_manager,
        )

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self.textChanged.connect(self._on_text_changed)

    @Slot()
    def _on_text_changed(self) -> None:
        """Handle text changes."""
        self.content_changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handle key press events, intercepting edits to protected content.

        Args:
            event: The key event.
        """
        # Check if this is an editing key
        if self._is_editing_key(event):
            if self._would_modify_protected(event):
                # Block the edit and emit signal
                return

        super().keyPressEvent(event)

    def _is_editing_key(self, event: QKeyEvent) -> bool:
        """
        Check if a key event would modify content.

        Args:
            event: The key event.

        Returns:
            True if the key would modify content.
        """
        key = event.key()

        # Navigation and modifier-only keys don't edit
        non_editing_keys = {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Control,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_Escape,
            Qt.Key.Key_CapsLock,
            Qt.Key.Key_NumLock,
            Qt.Key.Key_ScrollLock,
        }

        if key in non_editing_keys:
            return False

        # Check for Ctrl+C (copy), Ctrl+A (select all) - these don't edit
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key in {Qt.Key.Key_C, Qt.Key.Key_A}:
                return False

        return True

    def _would_modify_protected(self, event: QKeyEvent) -> bool:
        """
        Check if a key event would modify protected content.

        Args:
            event: The key event.

        Returns:
            True if the event would modify protected content.
        """
        cursor = self.textCursor()
        key = event.key()

        # Handle selection deletion
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            is_protected, region_id = self._protection_manager.is_range_protected(
                start, end
            )
            if is_protected and region_id:
                logger.debug(f"Selection overlaps protected region: {region_id}")
                self.protection_violated.emit(region_id, start)
                return True

        pos = cursor.position()

        # Handle backspace
        if key == Qt.Key.Key_Backspace:
            if pos > 0:
                is_protected, region_id = self._protection_manager.is_position_protected(
                    pos - 1
                )
                if is_protected and region_id:
                    logger.debug(f"Backspace into protected region: {region_id}")
                    self.protection_violated.emit(region_id, pos - 1)
                    return True

        # Handle delete
        elif key == Qt.Key.Key_Delete:
            is_protected, region_id = self._protection_manager.is_position_protected(pos)
            if is_protected and region_id:
                logger.debug(f"Delete in protected region: {region_id}")
                self.protection_violated.emit(region_id, pos)
                return True

        # Handle character insertion
        elif event.text():
            is_protected, region_id = self._protection_manager.is_position_protected(pos)
            if is_protected and region_id:
                logger.debug(f"Insert in protected region: {region_id}")
                self.protection_violated.emit(region_id, pos)
                return True

        return False

    def insertFromMimeData(self, source) -> None:
        """
        Handle paste operations, checking for protection.

        Args:
            source: The mime data being pasted.
        """
        cursor = self.textCursor()

        # Check if paste position is protected
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            is_protected, region_id = self._protection_manager.is_range_protected(
                start, end
            )
        else:
            pos = cursor.position()
            is_protected, region_id = self._protection_manager.is_position_protected(pos)
            start = pos

        if is_protected and region_id:
            logger.debug(f"Paste blocked in protected region: {region_id}")
            self.protection_violated.emit(region_id, start)
            return

        super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source) -> bool:
        """
        Check if mime data can be inserted.

        Args:
            source: The mime data.

        Returns:
            True if insertion is allowed.
        """
        cursor = self.textCursor()

        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            is_protected, _ = self._protection_manager.is_range_protected(start, end)
        else:
            pos = cursor.position()
            is_protected, _ = self._protection_manager.is_position_protected(pos)

        if is_protected:
            return False

        return super().canInsertFromMimeData(source)

    def set_content(self, markdown: str) -> None:
        """
        Set the editor content and re-parse protection regions.

        Args:
            markdown: The markdown content to display.
        """
        # Block signals to avoid spurious content_changed
        self.blockSignals(True)
        try:
            self.setPlainText(markdown)
            self._protection_manager.parse_regions(markdown)
        finally:
            self.blockSignals(False)

    def get_content(self) -> str:
        """
        Get the current markdown content.

        Returns:
            The plain text content.
        """
        return self.toPlainText()

    def get_introspection_data(self) -> dict[str, Any]:
        """
        Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with widget information.
        """
        return {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "object_name": self.objectName(),
            "class_name": self.__class__.__name__,
            "content_length": len(self.toPlainText()),
            "line_count": self.document().blockCount(),
            "cursor_position": self.textCursor().position(),
            "has_selection": self.textCursor().hasSelection(),
            "enabled": self.isEnabled(),
            "visible": self.isVisible(),
            "read_only": self.isReadOnly(),
        }
