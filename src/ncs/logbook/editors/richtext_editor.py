"""
WYSIWYG rich text editor with protection enforcement.

Provides a QTextEdit-based editor with markdown backing and
protection for designated content regions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import (
    QFont,
    QKeyEvent,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QTextEdit, QWidget

from ncs.logbook.style import LogbookStyles

if TYPE_CHECKING:
    from ncs.logbook.converter import MarkdownConverter
    from ncs.logbook.protection import ProtectionManager


class ProtectedBlockData(QTextBlockUserData):
    """
    User data attached to QTextBlocks to track protection status.

    Attributes:
        is_protected: Whether this block is protected.
        region_id: The ID of the protected region, if any.
    """

    def __init__(self, region_id: str | None = None) -> None:
        """
        Initialize the block data.

        Args:
            region_id: The ID of the protected region, or None.
        """
        super().__init__()
        self.region_id = region_id

    @property
    def is_protected(self) -> bool:
        """Whether this block is protected."""
        return self.region_id is not None


class RichTextEditor(QTextEdit):
    """
    WYSIWYG rich text editor with markdown backing.

    This editor provides:
    - Rich text editing with formatting
    - Markdown conversion on load/save
    - Visual highlighting of protected regions
    - Edit interception for protected content
    - Signal emission when protection is violated

    The editor stores content internally as HTML but converts to/from
    markdown for external use. Protected regions are tracked using
    QTextBlock userData.

    Attributes:
        widget_type: Type identifier for introspection.
        widget_description: Description for introspection.

    Signals:
        content_changed: Emitted when the content is modified.
        protection_violated(str, int): Emitted when an edit is attempted
            in a protected region. Args are (region_id, cursor_position).

    Example:
        >>> editor = RichTextEditor(protection_manager, converter)
        >>> editor.set_markdown("# Hello\\n\\nWorld")
        >>> editor.protection_violated.connect(lambda r, p: print(f"Blocked: {r}"))
    """

    widget_type: ClassVar[str] = "RichTextEditor"
    widget_description: ClassVar[str] = "WYSIWYG rich text editor with markdown support"

    # Signals
    content_changed = Signal()
    protection_violated = Signal(str, int)  # (region_id, cursor_position)

    def __init__(
        self,
        protection_manager: ProtectionManager,
        converter: MarkdownConverter,
        parent: QWidget | None = None,
    ) -> None:
        """
        Initialize the rich text editor.

        Args:
            protection_manager: Manager for protected content regions.
            converter: Markdown to HTML converter.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._protection_manager = protection_manager
        self._converter = converter
        self._protected_blocks: dict[int, str] = {}  # block_number -> region_id
        self._updating_content = False

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Configure the editor UI."""
        self.setStyleSheet(LogbookStyles.editor_base())
        self.setAcceptRichText(True)
        self.setPlaceholderText("Enter content...")

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self.textChanged.connect(self._on_text_changed)

    @Slot()
    def _on_text_changed(self) -> None:
        """Handle text changes."""
        if not self._updating_content:
            self.content_changed.emit()

    def set_markdown(self, markdown: str) -> None:
        """
        Load markdown content into the editor.

        Converts the markdown to HTML and loads it into the editor,
        then marks protected blocks.

        Args:
            markdown: The markdown content to display.
        """
        self._updating_content = True
        try:
            # Parse protected regions
            self._protection_manager.parse_regions(markdown)

            # Convert to HTML
            html = self._converter.markdown_to_html(markdown)

            # Clear document before loading new content to ensure clean state
            self.clear()

            # Load into editor
            self.setHtml(html)

            # Mark protected blocks
            self._mark_protected_blocks()

        finally:
            self._updating_content = False

    def get_markdown(self) -> str:
        """
        Extract markdown from the current content.

        Converts the HTML content back to markdown.

        Returns:
            The markdown content.
        """
        html = self.toHtml()
        return self._converter.html_to_markdown(html)

    def _mark_protected_blocks(self) -> None:
        """
        Mark QTextBlocks that correspond to protected regions.

        This method walks through the document and attaches
        ProtectedBlockData to blocks that fall within protected regions.
        """
        self._protected_blocks.clear()
        document = self.document()

        # Get document text to map positions
        full_text = document.toPlainText()

        regions = self._protection_manager.get_regions()
        if not regions:
            return

        # Walk through blocks
        block = document.begin()
        current_pos = 0

        while block.isValid():
            block_num = block.blockNumber()
            block_length = block.length()
            block_end = current_pos + block_length

            # Check if this block overlaps any protected region
            for region in regions:
                # Map region positions from markdown to document positions
                # This is approximate - we check if block overlaps
                if current_pos < region.end_offset and block_end > region.start_offset:
                    # Block overlaps with protected region
                    data = ProtectedBlockData(region.region_id)
                    block.setUserData(data)
                    self._protected_blocks[block_num] = region.region_id
                    logger.debug(
                        f"Marked block {block_num} as protected: {region.region_id}"
                    )
                    break

            current_pos = block_end
            block = block.next()

    def _is_cursor_in_protected(self, cursor: QTextCursor) -> tuple[bool, str | None]:
        """
        Check if a cursor is within a protected block.

        Args:
            cursor: The text cursor to check.

        Returns:
            Tuple of (is_protected, region_id or None).
        """
        block = cursor.block()
        user_data = block.userData()

        if isinstance(user_data, ProtectedBlockData) and user_data.is_protected:
            # Check if region is unlocked
            region = self._protection_manager.get_region(user_data.region_id)
            if region and not region.unlocked:
                return True, user_data.region_id

        return False, None

    def _is_selection_protected(
        self, cursor: QTextCursor
    ) -> tuple[bool, str | None]:
        """
        Check if a selection overlaps any protected blocks.

        Args:
            cursor: The text cursor with selection.

        Returns:
            Tuple of (is_protected, first_region_id or None).
        """
        if not cursor.hasSelection():
            return False, None

        start_block = self.document().findBlock(cursor.selectionStart())
        end_block = self.document().findBlock(cursor.selectionEnd())

        block = start_block
        while block.isValid():
            user_data = block.userData()
            if isinstance(user_data, ProtectedBlockData) and user_data.is_protected:
                region = self._protection_manager.get_region(user_data.region_id)
                if region and not region.unlocked:
                    return True, user_data.region_id

            if block == end_block:
                break
            block = block.next()

        return False, None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handle key press events, intercepting edits to protected content.

        Args:
            event: The key event.
        """
        if self._is_editing_key(event):
            if self._would_modify_protected(event):
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

        # Check selection
        if cursor.hasSelection():
            is_protected, region_id = self._is_selection_protected(cursor)
            if is_protected and region_id:
                logger.debug(f"Selection overlaps protected: {region_id}")
                self.protection_violated.emit(region_id, cursor.selectionStart())
                return True

        # Check cursor position for editing keys
        is_protected, region_id = self._is_cursor_in_protected(cursor)

        if key == Qt.Key.Key_Backspace:
            # Also check previous block if at start of block
            if cursor.atBlockStart():
                prev_block = cursor.block().previous()
                if prev_block.isValid():
                    user_data = prev_block.userData()
                    if isinstance(user_data, ProtectedBlockData) and user_data.is_protected:
                        region = self._protection_manager.get_region(user_data.region_id)
                        if region and not region.unlocked:
                            self.protection_violated.emit(
                                user_data.region_id, cursor.position() - 1
                            )
                            return True
            elif is_protected and region_id:
                self.protection_violated.emit(region_id, cursor.position())
                return True

        elif key == Qt.Key.Key_Delete:
            if is_protected and region_id:
                self.protection_violated.emit(region_id, cursor.position())
                return True
            # Check next block if at end of block
            if cursor.atBlockEnd():
                next_block = cursor.block().next()
                if next_block.isValid():
                    user_data = next_block.userData()
                    if isinstance(user_data, ProtectedBlockData) and user_data.is_protected:
                        region = self._protection_manager.get_region(user_data.region_id)
                        if region and not region.unlocked:
                            self.protection_violated.emit(
                                user_data.region_id, cursor.position()
                            )
                            return True

        elif event.text():
            if is_protected and region_id:
                self.protection_violated.emit(region_id, cursor.position())
                return True

        return False

    def insertFromMimeData(self, source) -> None:
        """
        Handle paste operations, checking for protection.

        Args:
            source: The mime data being pasted.
        """
        cursor = self.textCursor()

        if cursor.hasSelection():
            is_protected, region_id = self._is_selection_protected(cursor)
        else:
            is_protected, region_id = self._is_cursor_in_protected(cursor)

        if is_protected and region_id:
            logger.debug(f"Paste blocked in protected: {region_id}")
            self.protection_violated.emit(region_id, cursor.position())
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
            is_protected, _ = self._is_selection_protected(cursor)
        else:
            is_protected, _ = self._is_cursor_in_protected(cursor)

        if is_protected:
            return False

        return super().canInsertFromMimeData(source)

    # Formatting methods

    def toggle_bold(self) -> None:
        """Toggle bold formatting on the selection."""
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        fmt = QTextCharFormat()
        if cursor.charFormat().fontWeight() == QFont.Weight.Bold:
            fmt.setFontWeight(QFont.Weight.Normal)
        else:
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor.mergeCharFormat(fmt)

    def toggle_italic(self) -> None:
        """Toggle italic formatting on the selection."""
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        fmt = QTextCharFormat()
        fmt.setFontItalic(not cursor.charFormat().fontItalic())
        cursor.mergeCharFormat(fmt)

    def toggle_strikethrough(self) -> None:
        """Toggle strikethrough formatting on the selection."""
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not cursor.charFormat().fontStrikeOut())
        cursor.mergeCharFormat(fmt)

    def set_heading(self, level: int) -> None:
        """
        Set the current block as a heading.

        Args:
            level: The heading level (1-6), or 0 for normal text.
        """
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        block_fmt = cursor.blockFormat()
        char_fmt = cursor.charFormat()

        if level == 0:
            block_fmt.setHeadingLevel(0)
            char_fmt.setFontWeight(QFont.Weight.Normal)
            char_fmt.setFontPointSize(10)
        else:
            block_fmt.setHeadingLevel(level)
            char_fmt.setFontWeight(QFont.Weight.Bold)
            # Scale font size based on heading level
            sizes = {1: 24, 2: 20, 3: 16, 4: 14, 5: 12, 6: 10}
            char_fmt.setFontPointSize(sizes.get(level, 10))

        cursor.setBlockFormat(block_fmt)
        cursor.mergeCharFormat(char_fmt)

    def insert_link(self, url: str, text: str | None = None) -> None:
        """
        Insert a link at the current cursor position.

        Args:
            url: The link URL.
            text: The link text (defaults to URL if not provided).
        """
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        if text is None:
            text = url

        cursor.insertHtml(f'<a href="{url}">{text}</a>')

    def insert_code_block(self) -> None:
        """Insert a code block at the current cursor position."""
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        cursor.insertHtml("<pre><code></code></pre>")

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
            "protected_blocks": len(self._protected_blocks),
            "enabled": self.isEnabled(),
            "visible": self.isVisible(),
            "read_only": self.isReadOnly(),
        }
