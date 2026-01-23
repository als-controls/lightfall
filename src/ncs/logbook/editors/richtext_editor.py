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
    QTextCharFormat,
    QTextCursor,
    QUndoStack,
    QUndoCommand,
)
from PySide6.QtWidgets import QTextEdit, QWidget

from ncs.logbook.block_mapper import BlockMapper, BlockProtectionData
from ncs.logbook.position_mapper import MarkdownPositionMapper, PositionMapping
from ncs.logbook.style import LogbookStyles
from ncs.logbook.visual_protection import (
    VisualProtectionTracker,
    PROTECTED_START,
    PROTECTED_END,
)

if TYPE_CHECKING:
    from ncs.logbook.converter import MarkdownConverter
    from ncs.logbook.protection import ProtectionManager


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

        # Block-based mapper for stable position tracking
        self._block_mapper = BlockMapper()

        # Visual protection tracker using zero-width markers
        self._visual_tracker = VisualProtectionTracker()

        # Current state
        self._markdown: str = ""
        self._mapping: PositionMapping | None = None
        self._visual_to_md_map: list[int] | None = None  # Visual pos -> Markdown pos
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

            # Convert to HTML (this injects zero-width markers)
            html = self._converter.markdown_to_html(markdown)

            # Store cursor position before update (as visual position)
            old_cursor = self.textCursor()
            old_visual_pos = old_cursor.position()

            # Clear and reload
            self.clear()
            self.setHtml(html)

            # Rebuild visual protection tracker from the rendered text
            # The markers are now part of the visual text
            visual_text = self.toPlainText()
            self._visual_tracker.rebuild_from_text(
                visual_text,
                self._protection_manager.get_regions(),
            )

            # Build block-based mappings for stable position tracking
            self._block_mapper.build_mappings(markdown, self.document())

            # Attach protection data to each block for O(1) lookup
            for mapping in self._block_mapper.mappings:
                block = self.document().findBlockByNumber(mapping.visual_block_num)
                if block.isValid():
                    data = BlockProtectionData(
                        region_id=mapping.region_id,
                        md_line_num=mapping.md_line_start,
                        is_protected=mapping.is_protected,
                    )
                    block.setUserData(data)

            # Build accurate visual -> markdown position mapping (legacy, for formatting)
            self._visual_to_md_map = self._build_position_map(markdown, visual_text)

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

    def _build_position_map(self, markdown: str, visual_text: str) -> list[int]:
        """
        Build accurate visual_pos -> markdown_pos mapping.

        This builds a mapping by finding anchors (unique strings) in both texts
        and interpolating positions between anchors.

        The challenge: visual text differs from markdown in many ways:
        - Markdown syntax (# for headers, ** for bold) is stripped
        - HTML comments (<!-- PROTECTED:... -->) are stripped
        - Zero-width markers are added
        - Paragraph breaks differ (\\n\\n vs \\n)

        Strategy: Find words/tokens that exist in both and use them as anchors.

        Args:
            markdown: The source markdown text.
            visual_text: The rendered plain text from QTextEdit (with markers).

        Returns:
            List where index is visual position, value is markdown position.
        """
        import re

        # Strip zero-width markers from visual for matching
        clean_visual = visual_text.replace(PROTECTED_START, "").replace(PROTECTED_END, "")

        # Find word anchors that appear in both texts
        # Words are sequences of alphanumeric characters
        word_pattern = re.compile(r"\b\w+\b")

        visual_words = [(m.group(), m.start(), m.end()) for m in word_pattern.finditer(clean_visual)]
        md_words = [(m.group(), m.start(), m.end()) for m in word_pattern.finditer(markdown)]

        # Build anchor mapping: visual_start -> markdown_start for matching words
        anchors: list[tuple[int, int]] = []  # (visual_pos, md_pos)
        md_word_idx = 0

        for v_word, v_start, v_end in visual_words:
            # Find this word in markdown (starting from where we left off)
            while md_word_idx < len(md_words):
                m_word, m_start, m_end = md_words[md_word_idx]
                if m_word == v_word:
                    anchors.append((v_start, m_start))
                    md_word_idx += 1
                    break
                md_word_idx += 1

        # Add start and end anchors
        anchors.insert(0, (0, 0))
        anchors.append((len(clean_visual), len(markdown)))

        # Now build full position map by interpolating between anchors
        clean_to_md: list[int] = []
        anchor_idx = 0

        for clean_pos in range(len(clean_visual) + 1):
            # Find surrounding anchors
            while anchor_idx < len(anchors) - 1 and anchors[anchor_idx + 1][0] <= clean_pos:
                anchor_idx += 1

            v_anchor, m_anchor = anchors[anchor_idx]

            if anchor_idx + 1 < len(anchors):
                v_next, m_next = anchors[anchor_idx + 1]
                # Interpolate
                v_range = v_next - v_anchor
                m_range = m_next - m_anchor
                if v_range > 0:
                    offset = clean_pos - v_anchor
                    md_pos = m_anchor + int(offset * m_range / v_range)
                else:
                    md_pos = m_anchor
            else:
                # Past last anchor
                md_pos = len(markdown)

            clean_to_md.append(min(md_pos, len(markdown)))

        # Now map from actual visual (with markers) to markdown
        # by mapping through clean_visual
        visual_to_md: list[int] = []
        clean_pos = 0

        for vis_char in visual_text:
            if vis_char in (PROTECTED_START, PROTECTED_END):
                # Zero-width marker - map to current clean position's markdown pos
                if clean_pos < len(clean_to_md):
                    visual_to_md.append(clean_to_md[clean_pos])
                else:
                    visual_to_md.append(len(markdown))
            else:
                # Regular character
                if clean_pos < len(clean_to_md):
                    visual_to_md.append(clean_to_md[clean_pos])
                else:
                    visual_to_md.append(len(markdown))
                clean_pos += 1

        # Add end mapping
        visual_to_md.append(len(markdown))

        return visual_to_md

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

    def _is_cursor_in_protected(self, cursor: QTextCursor) -> tuple[bool, str | None]:
        """
        Check if a cursor is within a protected region.

        Uses block-based mapping for stable position tracking.

        Args:
            cursor: The text cursor to check.

        Returns:
            Tuple of (is_protected, region_id or None).
        """
        block_num = cursor.blockNumber()
        return self._block_mapper.is_block_protected(block_num)

    def _is_selection_protected(
        self, cursor: QTextCursor
    ) -> tuple[bool, str | None]:
        """
        Check if a selection overlaps any protected regions.

        Uses block-based mapping to check all blocks in the selection.

        Args:
            cursor: The text cursor with selection.

        Returns:
            Tuple of (is_protected, first_region_id or None).
        """
        if not cursor.hasSelection():
            return False, None

        # Get start and end blocks of the selection
        start_cursor = QTextCursor(cursor)
        start_cursor.setPosition(cursor.selectionStart())
        end_cursor = QTextCursor(cursor)
        end_cursor.setPosition(cursor.selectionEnd())

        start_block = start_cursor.blockNumber()
        end_block = end_cursor.blockNumber()

        # Check all blocks in the selection
        for block_num in range(start_block, end_block + 1):
            is_protected, region_id = self._block_mapper.is_block_protected(block_num)
            if is_protected:
                return True, region_id

        return False, None

    def _visual_to_md_pos(self, visual_pos: int) -> int:
        """Convert visual position to markdown position.

        Uses block-based mapping for stable position tracking.
        Falls back to anchor-based mapping if block mapping unavailable.
        """
        # Create a cursor at the visual position to get block info
        cursor = QTextCursor(self.document())
        cursor.setPosition(min(visual_pos, len(self.toPlainText())))

        block_num = cursor.blockNumber()
        offset_in_block = cursor.positionInBlock()

        return self._block_mapper.visual_to_md_pos(block_num, offset_in_block)

    def _visual_to_md_pos_legacy(self, visual_pos: int) -> int:
        """Legacy position conversion using anchor-based mapping.

        Kept for formatting operations that need precise character mapping.
        """
        if self._visual_to_md_map is None:
            # Fallback if map not built yet
            return visual_pos

        # Clamp to valid range
        if visual_pos < 0:
            return 0
        if visual_pos >= len(self._visual_to_md_map):
            return len(self._markdown)

        return self._visual_to_md_map[visual_pos]

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
        """Apply markdown edit, push to undo stack, and re-render.

        DEPRECATED: Use _apply_edit_and_rerender_block for stable cursor positioning.
        """
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

    def _apply_edit_and_rerender_block(
        self, new_markdown: str, block_num: int, offset_in_block: int
    ) -> None:
        """Apply markdown edit and re-render, restoring cursor using block-relative position.

        This method uses block-relative coordinates for cursor restoration, which
        is more stable across re-renders since block-relative offsets don't change
        when content in earlier blocks changes.

        Args:
            new_markdown: The new markdown content.
            block_num: Target block number for cursor.
            offset_in_block: Target offset within the block.
        """
        # For undo support, we still need visual positions
        # Calculate approximate visual position for undo command
        old_markdown = self._markdown
        old_cursor = self.textCursor()
        old_visual_pos = old_cursor.position()

        # Skip undo push if we're applying an undo/redo operation
        if not self._applying_undo:
            # Estimate new visual position for undo (approximate)
            # This is used by undo/redo which doesn't need block precision
            approx_visual_pos = old_visual_pos

            # Create and push undo command
            command = MarkdownEditCommand(
                self,
                old_markdown,
                new_markdown,
                old_visual_pos,
                approx_visual_pos,
                "Edit",
            )
            self._undo_stack.push(command)
            # Note: The redo() is called by push(), but we override cursor below

        # Apply the change
        self._markdown = new_markdown
        self.markdown_edit_requested.emit(new_markdown)
        self.render_markdown(new_markdown)

        # Restore cursor using block-relative position
        self._restore_cursor_block(block_num, offset_in_block)

        self.content_changed.emit()

    def _restore_cursor_block(self, block_num: int, offset_in_block: int) -> None:
        """Restore cursor to a block-relative position.

        Args:
            block_num: The target block number.
            offset_in_block: Offset within the block.
        """
        doc = self.document()
        block = doc.findBlockByNumber(block_num)

        if block.isValid():
            # Calculate absolute position from block start + offset
            block_start = block.position()
            block_length = block.length() - 1  # -1 for block separator

            # Clamp offset to block bounds
            safe_offset = min(offset_in_block, max(0, block_length))
            new_pos = block_start + safe_offset

            cursor = self.textCursor()
            cursor.setPosition(new_pos)
            self.setTextCursor(cursor)
        else:
            # Fallback: move to end of document
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)

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
                # Check if backspace would delete into protected region
                # If we're at the start of a block, backspace affects the previous block
                if cursor.atBlockStart() and cursor.blockNumber() > 0:
                    prev_block = cursor.blockNumber() - 1
                    is_prev_protected, prev_region_id = self._block_mapper.is_block_protected(prev_block)
                    if is_prev_protected and prev_region_id:
                        self.protection_violated.emit(prev_region_id, cursor.position() - 1)
                        return
                # Also check if current block is protected (deleting within it)
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
                # Check if delete would affect protected region
                # Delete removes the character AT the cursor position
                if is_protected and region_id:
                    self.protection_violated.emit(region_id, cursor.position())
                    return
                # If we're at end of block, delete affects the next block
                if cursor.atBlockEnd():
                    next_block = cursor.blockNumber() + 1
                    is_next_protected, next_region_id = self._block_mapper.is_block_protected(next_block)
                    if is_next_protected and next_region_id:
                        self.protection_violated.emit(next_region_id, cursor.position())
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
        # Use block-relative coordinates for stable position tracking
        block_num = cursor.blockNumber()
        offset_in_block = cursor.positionInBlock()

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
                # Restore cursor using block-relative position
                self._apply_edit_and_rerender_block(
                    new_markdown, block_num, offset_in_block + len(text)
                )
                return

        # Convert visual position to markdown position using block-relative coords
        md_pos = self._block_mapper.visual_to_md_pos(block_num, offset_in_block)
        new_markdown = self._markdown[:md_pos] + text + self._markdown[md_pos:]

        # Restore cursor using block-relative position
        self._apply_edit_and_rerender_block(
            new_markdown, block_num, offset_in_block + len(text)
        )

    def _handle_paragraph_break(self, cursor: QTextCursor) -> None:
        """Handle Enter key - insert paragraph break in markdown.

        This needs special handling because QTextEdit's visual representation
        of paragraphs doesn't match markdown newlines character-for-character.
        """
        block_num = cursor.blockNumber()
        offset_in_block = cursor.positionInBlock()

        # Map to markdown position using block-relative coords
        md_pos = self._block_mapper.visual_to_md_pos(block_num, offset_in_block)

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

        # After paragraph break, cursor goes to start of the next block
        self._apply_edit_and_rerender_block(new_markdown, block_num + 1, 0)

    def _handle_backspace(self, cursor: QTextCursor) -> None:
        """Handle backspace at cursor position."""
        block_num = cursor.blockNumber()
        offset_in_block = cursor.positionInBlock()

        if cursor.position() <= 0:
            return

        # Check if we're at start of a block (would delete paragraph break)
        if cursor.atBlockStart() and block_num > 0:
            # Deleting a paragraph break - need to remove \n\n from markdown
            md_pos = self._block_mapper.visual_to_md_pos(block_num, 0)
            # Find the paragraph break before this position
            # It should be \n\n just before md_pos
            if md_pos >= 2 and self._markdown[md_pos-2:md_pos] == "\n\n":
                new_markdown = self._markdown[:md_pos-2] + self._markdown[md_pos:]
                # After merging paragraphs, cursor goes to end of previous block
                # Get the previous block's length from the mapping
                prev_mapping = self._block_mapper.get_block_mapping(block_num - 1)
                if prev_mapping:
                    prev_block_len = len(prev_mapping.visual_text)
                    self._apply_edit_and_rerender_block(
                        new_markdown, block_num - 1, prev_block_len
                    )
                else:
                    # Fallback
                    self._apply_edit_and_rerender_block(new_markdown, block_num - 1, 0)
                return

        # Regular backspace - delete one character
        md_pos = self._block_mapper.visual_to_md_pos(block_num, offset_in_block)
        if md_pos > 0:
            new_markdown = self._markdown[:md_pos-1] + self._markdown[md_pos:]
            # Cursor moves back one position in same block
            self._apply_edit_and_rerender_block(
                new_markdown, block_num, max(0, offset_in_block - 1)
            )

    def _handle_delete(self, cursor: QTextCursor) -> None:
        """Handle delete at cursor position."""
        block_num = cursor.blockNumber()
        offset_in_block = cursor.positionInBlock()
        md_pos = self._block_mapper.visual_to_md_pos(block_num, offset_in_block)

        if md_pos >= len(self._markdown):
            return

        # Check if we're at end of a block (would delete paragraph break)
        if cursor.atBlockEnd():
            # Deleting forward into paragraph break - remove \n\n
            if self._markdown[md_pos:md_pos+2] == "\n\n":
                new_markdown = self._markdown[:md_pos] + self._markdown[md_pos+2:]
                # Cursor stays at same position
                self._apply_edit_and_rerender_block(new_markdown, block_num, offset_in_block)
                return

        # Regular delete - delete one character
        new_markdown = self._markdown[:md_pos] + self._markdown[md_pos+1:]
        # Cursor stays at same position
        self._apply_edit_and_rerender_block(new_markdown, block_num, offset_in_block)

    def _handle_delete_selection(self, cursor: QTextCursor) -> None:
        """Handle deletion of selected text."""
        # Get start position as block-relative
        start_cursor = QTextCursor(cursor)
        start_cursor.setPosition(cursor.selectionStart())
        start_block = start_cursor.blockNumber()
        start_offset = start_cursor.positionInBlock()

        # Get end position
        end_cursor = QTextCursor(cursor)
        end_cursor.setPosition(cursor.selectionEnd())
        end_block = end_cursor.blockNumber()
        end_offset = end_cursor.positionInBlock()

        # Convert to markdown positions using block-relative coords
        md_start = self._block_mapper.visual_to_md_pos(start_block, start_offset)
        md_end = self._block_mapper.visual_to_md_pos(end_block, end_offset)

        new_markdown = self._markdown[:md_start] + self._markdown[md_end:]

        # Cursor goes to start of selection
        self._apply_edit_and_rerender_block(new_markdown, start_block, start_offset)

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
            # Strip any zero-width protection markers from pasted text
            text = VisualProtectionTracker.strip_markers(text)
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
            "protected_regions": len(self._visual_tracker.get_regions()),
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
