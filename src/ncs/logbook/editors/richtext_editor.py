"""
WYSIWYG rich text editor with markdown as source of truth.

Provides a QTextEdit-based editor that displays rendered markdown
and intercepts all edits, translating them to markdown operations.
The markdown content is the single source of truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import (
    QFont,
    QKeyEvent,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QTextEdit, QWidget

from ncs.logbook.position_mapper import MarkdownPositionMapper, PositionMapping
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
    WYSIWYG rich text editor with markdown as source of truth.

    This editor provides:
    - Rich text display rendered from markdown
    - Edit interception that translates to markdown operations
    - Visual highlighting of protected regions
    - Signal emission when protection is violated

    The editor does NOT store content internally. All content is managed
    externally via markdown, and the editor re-renders after each edit.

    Attributes:
        widget_type: Type identifier for introspection.
        widget_description: Description for introspection.

    Signals:
        content_changed: Emitted when the content is modified.
        protection_violated(str, int): Emitted when an edit is attempted
            in a protected region. Args are (region_id, cursor_position).
        markdown_edit_requested(str): Emitted with the new markdown content
            when an edit operation completes. The parent widget should
            update its markdown and call render_markdown().

    Example:
        >>> editor = RichTextEditor(protection_manager, converter)
        >>> editor.markdown_edit_requested.connect(widget.on_markdown_edit)
        >>> editor.render_markdown("# Hello\\n\\nWorld")
    """

    widget_type: ClassVar[str] = "RichTextEditor"
    widget_description: ClassVar[str] = "WYSIWYG rich text editor with markdown source of truth"

    # Signals
    content_changed = Signal()
    protection_violated = Signal(str, int)  # (region_id, cursor_position)
    markdown_edit_requested = Signal(str)  # new markdown content

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
        self._position_mapper = MarkdownPositionMapper()

        # Current state
        self._markdown: str = ""
        self._mapping: PositionMapping | None = None
        self._protected_blocks: dict[int, str] = {}  # block_number -> region_id
        self._updating_content = False

        # Debounce timer for re-rendering
        self._render_timer: QTimer | None = None
        self._pending_markdown: str | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Configure the editor UI."""
        self.setStyleSheet(LogbookStyles.editor_base())
        self.setAcceptRichText(True)
        self.setPlaceholderText("Enter content...")

    def render_markdown(self, markdown: str) -> None:
        """
        Render markdown content into the editor.

        This replaces the current display with the rendered markdown.
        Call this whenever the markdown source changes.

        Args:
            markdown: The markdown content to render.
        """
        self._updating_content = True
        try:
            # Store markdown and build position mapping
            self._markdown = markdown
            self._mapping = self._position_mapper.build_map(markdown)

            # Parse protected regions
            self._protection_manager.parse_regions(markdown)

            # Convert to HTML
            html = self._converter.markdown_to_html(markdown)

            # Store cursor position before update (as visual position)
            old_cursor = self.textCursor()
            old_visual_pos = old_cursor.position()

            # Clear and reload
            self.clear()
            self.setHtml(html)

            # Mark protected blocks
            self._mark_protected_blocks()

            # Restore cursor position
            self._restore_cursor_position(old_visual_pos)

        finally:
            self._updating_content = False

    def _restore_cursor_position(self, visual_pos: int) -> None:
        """Restore cursor to approximate visual position after re-render."""
        cursor = self.textCursor()
        doc_length = len(self.toPlainText())
        new_pos = min(visual_pos, doc_length)
        cursor.setPosition(new_pos)
        self.setTextCursor(cursor)

    def get_markdown(self) -> str:
        """
        Get the current markdown content.

        Returns:
            The markdown content (source of truth).
        """
        return self._markdown

    # Legacy compatibility method
    def set_markdown(self, markdown: str) -> None:
        """
        Set markdown content (legacy compatibility).

        This is an alias for render_markdown().

        Args:
            markdown: The markdown content to display.
        """
        self.render_markdown(markdown)

    def _mark_protected_blocks(self) -> None:
        """
        Mark QTextBlocks that correspond to protected regions.

        This method walks through the document and attaches
        ProtectedBlockData to blocks that fall within protected regions.
        """
        self._protected_blocks.clear()
        document = self.document()

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
                if current_pos < region.end_offset and block_end > region.start_offset:
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

    def _visual_to_md_pos(self, visual_pos: int) -> int:
        """Convert visual position to markdown position."""
        if self._mapping is None:
            return visual_pos
        return self._position_mapper.visual_to_markdown(self._mapping, visual_pos)

    def _apply_edit_and_rerender(self, new_markdown: str, new_visual_pos: int) -> None:
        """Apply markdown edit and re-render."""
        self._markdown = new_markdown
        self.markdown_edit_requested.emit(new_markdown)
        self.render_markdown(new_markdown)

        # Restore cursor to new position
        cursor = self.textCursor()
        doc_length = len(self.toPlainText())
        cursor.setPosition(min(new_visual_pos, doc_length))
        self.setTextCursor(cursor)

        self.content_changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Handle key press events, intercepting edits and translating to markdown.

        Args:
            event: The key event.
        """
        # Non-editing keys pass through
        if not self._is_editing_key(event):
            super().keyPressEvent(event)
            return

        # Check for protection
        cursor = self.textCursor()
        key = event.key()

        # Check selection protection
        if cursor.hasSelection():
            is_protected, region_id = self._is_selection_protected(cursor)
            if is_protected and region_id:
                logger.debug(f"Selection overlaps protected: {region_id}")
                self.protection_violated.emit(region_id, cursor.selectionStart())
                return

        # Check cursor position protection
        is_protected, region_id = self._is_cursor_in_protected(cursor)

        # Handle backspace
        if key == Qt.Key.Key_Backspace:
            if cursor.hasSelection():
                self._handle_delete_selection(cursor)
            elif cursor.position() > 0:
                # Check if deleting into protected region
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
                                return
                elif is_protected and region_id:
                    self.protection_violated.emit(region_id, cursor.position())
                    return

                self._handle_backspace(cursor)
            return

        # Handle delete
        if key == Qt.Key.Key_Delete:
            if cursor.hasSelection():
                self._handle_delete_selection(cursor)
            else:
                if is_protected and region_id:
                    self.protection_violated.emit(region_id, cursor.position())
                    return
                # Check if deleting into protected region
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
                                return

                self._handle_delete(cursor)
            return

        # Handle enter
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if is_protected and region_id:
                self.protection_violated.emit(region_id, cursor.position())
                return
            if cursor.hasSelection():
                self._handle_delete_selection(cursor)
                cursor = self.textCursor()
            self._handle_insert(cursor, "\n")
            return

        # Handle regular text input
        text = event.text()
        if text and text.isprintable():
            if is_protected and region_id:
                self.protection_violated.emit(region_id, cursor.position())
                return
            if cursor.hasSelection():
                self._handle_delete_selection(cursor)
                cursor = self.textCursor()
            self._handle_insert(cursor, text)
            return

        # Pass through unhandled events
        super().keyPressEvent(event)

    def _handle_insert(self, cursor: QTextCursor, text: str) -> None:
        """Handle text insertion at cursor position."""
        visual_pos = cursor.position()
        new_markdown, new_visual_pos = self._position_mapper.apply_insert(
            self._markdown, visual_pos, text, self._mapping
        )
        self._apply_edit_and_rerender(new_markdown, new_visual_pos)

    def _handle_backspace(self, cursor: QTextCursor) -> None:
        """Handle backspace at cursor position."""
        visual_pos = cursor.position()
        if visual_pos > 0:
            new_markdown, new_visual_pos = self._position_mapper.apply_delete(
                self._markdown, visual_pos - 1, visual_pos, self._mapping
            )
            self._apply_edit_and_rerender(new_markdown, new_visual_pos)

    def _handle_delete(self, cursor: QTextCursor) -> None:
        """Handle delete at cursor position."""
        visual_pos = cursor.position()
        plain_len = len(self._mapping.plain_text) if self._mapping else len(self._markdown)
        if visual_pos < plain_len:
            new_markdown, new_visual_pos = self._position_mapper.apply_delete(
                self._markdown, visual_pos, visual_pos + 1, self._mapping
            )
            self._apply_edit_and_rerender(new_markdown, new_visual_pos)

    def _handle_delete_selection(self, cursor: QTextCursor) -> None:
        """Handle deletion of selected text."""
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        new_markdown, new_visual_pos = self._position_mapper.apply_delete(
            self._markdown, start, end, self._mapping
        )
        self._apply_edit_and_rerender(new_markdown, new_visual_pos)

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

        # Get plain text from clipboard
        text = source.text() if source.hasText() else ""
        if text:
            if cursor.hasSelection():
                self._handle_delete_selection(cursor)
                cursor = self.textCursor()
            self._handle_insert(cursor, text)

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

        return source.hasText()

    # Formatting methods - these now operate on markdown

    def set_bold(self, bold: bool) -> None:
        """Apply or remove bold formatting to the selection.

        Args:
            bold: Whether text should be bold.
        """
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        if not cursor.hasSelection():
            return

        self._apply_formatting(cursor, "**", "**", bold)

    def set_italic(self, italic: bool) -> None:
        """Apply or remove italic formatting to the selection.

        Args:
            italic: Whether text should be italic.
        """
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        if not cursor.hasSelection():
            return

        self._apply_formatting(cursor, "*", "*", italic)

    def set_strikethrough(self, strikethrough: bool) -> None:
        """Apply or remove strikethrough formatting to the selection.

        Args:
            strikethrough: Whether text should have strikethrough.
        """
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        if not cursor.hasSelection():
            return

        self._apply_formatting(cursor, "~~", "~~", strikethrough)

    def _apply_formatting(
        self, cursor: QTextCursor, prefix: str, suffix: str, apply: bool
    ) -> None:
        """
        Apply or remove formatting to the selection.

        Args:
            cursor: Text cursor with selection.
            prefix: Markdown prefix (e.g., "**").
            suffix: Markdown suffix (e.g., "**").
            apply: True to apply formatting, False to remove.
        """
        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        if apply:
            # Apply formatting by wrapping with prefix/suffix
            new_markdown = self._position_mapper.apply_formatting(
                self._markdown, start, end, prefix, suffix, self._mapping
            )
        else:
            # Remove formatting - need to find and remove the markers
            new_markdown = self._remove_formatting(start, end, prefix, suffix)
            if new_markdown is None:
                return

        self._apply_edit_and_rerender(new_markdown, end + len(prefix))

        # Restore selection (approximately)
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end + len(prefix) if apply else end - len(prefix), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def _remove_formatting(
        self, visual_start: int, visual_end: int, prefix: str, suffix: str
    ) -> str | None:
        """
        Remove formatting markers around a selection.

        Args:
            visual_start: Start of selection in visual view.
            visual_end: End of selection in visual view.
            prefix: Markdown prefix to remove.
            suffix: Markdown suffix to remove.

        Returns:
            New markdown with formatting removed, or None if not found.
        """
        if self._mapping is None:
            return None

        md_start = self._position_mapper.visual_to_markdown(self._mapping, visual_start)
        md_end = self._position_mapper.visual_to_markdown(self._mapping, visual_end)

        # Look for prefix before md_start and suffix after md_end
        prefix_pos = self._markdown.rfind(prefix, 0, md_start + 1)
        suffix_pos = self._markdown.find(suffix, md_end - 1)

        if prefix_pos == -1 or suffix_pos == -1:
            return None

        # Remove the markers
        new_md = (
            self._markdown[:prefix_pos]
            + self._markdown[prefix_pos + len(prefix) : suffix_pos]
            + self._markdown[suffix_pos + len(suffix) :]
        )
        return new_md

    def toggle_bold(self) -> None:
        """Toggle bold formatting on the selection."""
        cursor = self.textCursor()
        # Check if currently bold by looking at char format
        is_bold = cursor.charFormat().fontWeight() == QFont.Weight.Bold
        self.set_bold(not is_bold)

    def toggle_italic(self) -> None:
        """Toggle italic formatting on the selection."""
        cursor = self.textCursor()
        self.set_italic(not cursor.charFormat().fontItalic())

    def toggle_strikethrough(self) -> None:
        """Toggle strikethrough formatting on the selection."""
        cursor = self.textCursor()
        self.set_strikethrough(not cursor.charFormat().fontStrikeOut())

    def set_heading(self, level: int) -> None:
        """
        Set the current line as a heading.

        Args:
            level: The heading level (1-6), or 0 for normal text.
        """
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        # Get line start position in visual view
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        line_start = cursor.position()

        # Convert to markdown position
        if self._mapping is None:
            return

        md_line_start = self._position_mapper.visual_to_markdown(self._mapping, line_start)

        # Find start of line in markdown
        line_begin = self._markdown.rfind("\n", 0, md_line_start)
        if line_begin == -1:
            line_begin = 0
        else:
            line_begin += 1  # Skip the newline

        # Check if line already has a heading
        line_content = self._markdown[line_begin:]
        line_end = line_content.find("\n")
        if line_end == -1:
            line_end = len(line_content)
        line_content = line_content[:line_end]

        # Remove existing heading markers
        import re
        stripped_line = re.sub(r"^#{1,6}\s*", "", line_content)

        # Build new line
        if level == 0:
            new_line = stripped_line
        else:
            new_line = "#" * level + " " + stripped_line

        # Replace the line in markdown
        new_markdown = (
            self._markdown[:line_begin]
            + new_line
            + self._markdown[line_begin + len(line_content) :]
        )

        self._apply_edit_and_rerender(new_markdown, line_start)

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

        link_md = f"[{text}]({url})"

        if cursor.hasSelection():
            self._handle_delete_selection(cursor)
            cursor = self.textCursor()

        self._handle_insert(cursor, link_md)

    def insert_code_block(self) -> None:
        """Insert a code block at the current cursor position."""
        cursor = self.textCursor()
        is_protected, _ = self._is_cursor_in_protected(cursor)
        if is_protected:
            return

        code_md = "\n```\n\n```\n"

        if cursor.hasSelection():
            self._handle_delete_selection(cursor)
            cursor = self.textCursor()

        self._handle_insert(cursor, code_md)

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
            "content_length": len(self._markdown),
            "line_count": self.document().blockCount(),
            "cursor_position": self.textCursor().position(),
            "has_selection": self.textCursor().hasSelection(),
            "protected_blocks": len(self._protected_blocks),
            "enabled": self.isEnabled(),
            "visible": self.isVisible(),
            "read_only": self.isReadOnly(),
        }
