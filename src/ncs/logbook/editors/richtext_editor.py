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
from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QFont,
    QKeyEvent,
    QMouseEvent,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
    QUndoStack,
    QUndoCommand,
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


class MarkdownEditCommand(QUndoCommand):
    """Undo command for markdown edits."""

    def __init__(
        self,
        editor: "RichTextEditor",
        old_markdown: str,
        new_markdown: str,
        old_cursor_pos: int,
        new_cursor_pos: int,
        description: str = "Edit",
    ) -> None:
        super().__init__(description)
        self._editor = editor
        self._old_markdown = old_markdown
        self._new_markdown = new_markdown
        self._old_cursor_pos = old_cursor_pos
        self._new_cursor_pos = new_cursor_pos

    def redo(self) -> None:
        """Apply the edit."""
        self._editor._apply_markdown_state(self._new_markdown, self._new_cursor_pos)

    def undo(self) -> None:
        """Revert the edit."""
        self._editor._apply_markdown_state(self._old_markdown, self._old_cursor_pos)


class RichTextEditor(QTextEdit):
    """
    WYSIWYG rich text editor with markdown as source of truth.

    This editor provides:
    - Rich text display rendered from markdown
    - Edit interception that translates to markdown operations
    - Visual highlighting of protected regions
    - Signal emission when protection is violated
    - Undo/redo support for markdown edits

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
    action_group_clicked = Signal(str)  # region_id

    # Undo stack settings
    MAX_UNDO_STATES: ClassVar[int] = 100

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

        # Undo/redo stack
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(100)
        self._applying_undo = False  # Flag to prevent nested undo pushes

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
        Whitespace-only blocks are never marked as protected to allow
        users to type after protected content.
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
            block_text = block.text()

            # Never mark whitespace-only blocks as protected
            # This ensures users can always type after protected regions
            if block_text.strip() in ("", "\u00a0"):
                current_pos = block_end
                block = block.next()
                continue

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
        """Convert visual position to markdown position.

        This accounts for the difference between QTextEdit's visual representation
        (single newline between paragraphs) and markdown (double newline).
        """
        if self._mapping is None:
            return visual_pos

        # Count how many paragraph breaks (blocks) precede this position
        # Each paragraph break in markdown is \n\n but renders as single \n in QTextEdit
        cursor = self.textCursor()
        cursor.setPosition(visual_pos)
        block_num = cursor.blockNumber()

        # Add one character per paragraph break we've passed
        # (to account for \n\n in markdown vs \n in visual)
        adjusted_pos = visual_pos + block_num

        return self._position_mapper.visual_to_markdown(self._mapping, adjusted_pos)

    def _apply_markdown_state(self, markdown: str, visual_pos: int) -> None:
        """
        Apply a markdown state without pushing to undo stack.

        This is called by undo/redo commands to restore state.

        Args:
            markdown: The markdown content to set.
            visual_pos: The cursor position to restore.
        """
        self._applying_undo = True
        try:
            self._markdown = markdown
            self.markdown_edit_requested.emit(markdown)
            self.render_markdown(markdown)

            # Restore cursor
            cursor = self.textCursor()
            doc_length = len(self.toPlainText())
            cursor.setPosition(min(visual_pos, doc_length))
            self.setTextCursor(cursor)

            self.content_changed.emit()
        finally:
            self._applying_undo = False

    def _apply_edit_and_rerender(self, new_markdown: str, new_visual_pos: int) -> None:
        """Apply markdown edit, push to undo stack, and re-render."""
        # Skip undo push if we're applying an undo/redo operation
        if not self._applying_undo:
            # Get current state for undo
            old_markdown = self._markdown
            old_visual_pos = self.textCursor().position()

            # Create and push undo command
            command = MarkdownEditCommand(
                self,
                old_markdown,
                new_markdown,
                old_visual_pos,
                new_visual_pos,
                "Edit",
            )
            self._undo_stack.push(command)
            # The command's redo() is called automatically by push()
        else:
            # Direct apply without undo (called from undo command)
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
        key = event.key()
        modifiers = event.modifiers()

        # Handle undo/redo shortcuts
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    # Ctrl+Shift+Z = Redo
                    self._undo_stack.redo()
                else:
                    # Ctrl+Z = Undo
                    self._undo_stack.undo()
                return
            elif key == Qt.Key.Key_Y:
                # Ctrl+Y = Redo
                self._undo_stack.redo()
                return

        # Non-editing keys pass through
        if not self._is_editing_key(event):
            super().keyPressEvent(event)
            return

        # Check for protection
        cursor = self.textCursor()

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

        # Handle enter - insert paragraph break in markdown
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if is_protected and region_id:
                self.protection_violated.emit(region_id, cursor.position())
                return
            if cursor.hasSelection():
                self._handle_delete_selection(cursor)
                cursor = self.textCursor()
            # Use double newline for proper markdown paragraph break
            self._handle_paragraph_break(cursor)
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

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """
        Handle mouse release events to detect clicks on links.

        Uses QTextEdit.anchorAt() to check if the click was on a link,
        which is more reliable than tracking block user data.

        Args:
            event: The mouse event.
        """
        # Check if we clicked on an anchor (link)
        anchor = self.anchorAt(event.pos())
        if anchor:
            url = QUrl(anchor)
            self._on_anchor_clicked(url)
            # Don't pass through for action links - we handled it
            if url.scheme() == "ncs":
                return

        # Normal click handling
        super().mouseReleaseEvent(event)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        """
        Handle anchor (link) clicks, detecting action group links.

        The action group links use a custom URL scheme: ncs://action/{group_id}

        Args:
            url: The clicked URL.
        """
        if url.scheme() == "ncs" and url.host() == "action":
            # Extract the group ID from the path
            group_id = url.path().lstrip("/")
            region_id = f"action-{group_id}"
            logger.debug(f"Action group link clicked: {region_id}")
            self.action_group_clicked.emit(region_id)
        else:
            # For other links, could open externally or emit another signal
            logger.debug(f"Link clicked: {url.toString()}")

    def _handle_insert(self, cursor: QTextCursor, text: str) -> None:
        """Handle text insertion at cursor position."""
        visual_pos = cursor.position()

        # Check if we're in a placeholder paragraph (contains only nbsp)
        # If so, replace the placeholder instead of inserting
        block = cursor.block()
        block_text = block.text()
        if block_text.strip() in ("", "\u00a0") and cursor.atBlockStart():
            # Find this block's position in markdown and replace the placeholder
            # The placeholder paragraph is "\n\n\xa0" at the end
            if self._markdown.endswith("\n\n\u00a0") or self._markdown.endswith("\n\n "):
                # Replace the trailing placeholder with the new text
                base = self._markdown.rstrip("\u00a0 ")
                if base.endswith("\n\n"):
                    new_markdown = base + text
                else:
                    new_markdown = base + "\n\n" + text
                self._apply_edit_and_rerender(new_markdown, visual_pos + len(text))
                return

        # Convert visual position to markdown position (accounting for paragraph breaks)
        md_pos = self._visual_to_md_pos(visual_pos)
        new_markdown = self._markdown[:md_pos] + text + self._markdown[md_pos:]
        self._apply_edit_and_rerender(new_markdown, visual_pos + len(text))

    def _handle_paragraph_break(self, cursor: QTextCursor) -> None:
        """Handle Enter key - insert paragraph break in markdown.

        This needs special handling because QTextEdit's visual representation
        of paragraphs doesn't match markdown newlines character-for-character.
        """
        visual_pos = cursor.position()
        current_block = cursor.blockNumber()

        # Map to markdown position (accounting for paragraph breaks)
        md_pos = self._visual_to_md_pos(visual_pos)

        # Check if we're at the end (nothing after cursor in markdown)
        at_end = md_pos >= len(self._markdown) or self._markdown[md_pos:].strip() == ""

        if at_end:
            # At end of content - insert newlines and a non-breaking space placeholder
            # The nbsp ensures a new paragraph is created in HTML rendering
            # It will be replaced/removed when user types
            new_markdown = self._markdown[:md_pos].rstrip() + "\n\n\u00a0"
        else:
            # In middle of content - just insert double newline
            new_markdown = self._markdown[:md_pos] + "\n\n" + self._markdown[md_pos:]

        # Update and re-render
        self._markdown = new_markdown
        self.markdown_edit_requested.emit(new_markdown)
        self.render_markdown(new_markdown)

        # Position cursor at start of new paragraph (next block)
        new_cursor = self.textCursor()
        new_cursor.movePosition(QTextCursor.MoveOperation.Start)
        for _ in range(current_block + 1):
            new_cursor.movePosition(QTextCursor.MoveOperation.NextBlock)
        self.setTextCursor(new_cursor)

        self.content_changed.emit()

    def _handle_backspace(self, cursor: QTextCursor) -> None:
        """Handle backspace at cursor position."""
        visual_pos = cursor.position()
        if visual_pos <= 0:
            return

        # Check if we're at start of a block (would delete paragraph break)
        if cursor.atBlockStart() and cursor.blockNumber() > 0:
            # Deleting a paragraph break - need to remove \n\n from markdown
            md_pos = self._visual_to_md_pos(visual_pos)
            # Find the paragraph break before this position
            # It should be \n\n just before md_pos
            if md_pos >= 2 and self._markdown[md_pos-2:md_pos] == "\n\n":
                new_markdown = self._markdown[:md_pos-2] + self._markdown[md_pos:]
                self._apply_edit_and_rerender(new_markdown, visual_pos - 1)
                return

        # Regular backspace - delete one character
        md_pos = self._visual_to_md_pos(visual_pos)
        if md_pos > 0:
            new_markdown = self._markdown[:md_pos-1] + self._markdown[md_pos:]
            self._apply_edit_and_rerender(new_markdown, visual_pos - 1)

    def _handle_delete(self, cursor: QTextCursor) -> None:
        """Handle delete at cursor position."""
        visual_pos = cursor.position()
        md_pos = self._visual_to_md_pos(visual_pos)

        if md_pos >= len(self._markdown):
            return

        # Check if we're at end of a block (would delete paragraph break)
        if cursor.atBlockEnd():
            # Deleting forward into paragraph break - remove \n\n
            if self._markdown[md_pos:md_pos+2] == "\n\n":
                new_markdown = self._markdown[:md_pos] + self._markdown[md_pos+2:]
                self._apply_edit_and_rerender(new_markdown, visual_pos)
                return

        # Regular delete - delete one character
        new_markdown = self._markdown[:md_pos] + self._markdown[md_pos+1:]
        self._apply_edit_and_rerender(new_markdown, visual_pos)

    def _handle_delete_selection(self, cursor: QTextCursor) -> None:
        """Handle deletion of selected text."""
        # Get visual positions
        vis_start = cursor.selectionStart()
        vis_end = cursor.selectionEnd()

        # Convert to markdown positions
        # For start, use a cursor at the start position
        temp_cursor = self.textCursor()
        temp_cursor.setPosition(vis_start)
        md_start = self._visual_to_md_pos(vis_start)

        temp_cursor.setPosition(vis_end)
        md_end = self._visual_to_md_pos(vis_end)

        new_markdown = self._markdown[:md_start] + self._markdown[md_end:]
        self._apply_edit_and_rerender(new_markdown, vis_start)

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
            "can_undo": self.can_undo(),
            "can_redo": self.can_redo(),
        }

    # Undo/redo methods

    def undo(self) -> None:
        """Undo the last edit."""
        self._undo_stack.undo()

    def redo(self) -> None:
        """Redo the last undone edit."""
        self._undo_stack.redo()

    def can_undo(self) -> bool:
        """Check if undo is available."""
        return self._undo_stack.canUndo()

    def can_redo(self) -> bool:
        """Check if redo is available."""
        return self._undo_stack.canRedo()

    def clear_undo_stack(self) -> None:
        """Clear the undo/redo history."""
        self._undo_stack.clear()

    @property
    def undo_stack(self) -> QUndoStack:
        """Get the undo stack for external use (e.g., menu actions)."""
        return self._undo_stack
