"""
Syntax highlighter for markdown with protected region highlighting.

Provides visual feedback for markdown syntax and protected content regions
in the raw markdown editor.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from lightfall.logbook.style import (
    get_blockquote_color,
    get_code_background_color,
    get_code_text_color,
    get_emphasis_color,
    get_header_color,
    get_link_color,
    get_marker_color,
    get_protected_background_color,
    get_protected_border_color,
    get_protected_text_color,
    get_strong_color,
)

if TYPE_CHECKING:
    from lightfall.logbook.protection import ProtectionManager


class ProtectedMarkdownHighlighter(QSyntaxHighlighter):
    """
    Syntax highlighter for markdown with protected region support.

    Highlights:
    - Headers (#, ##, etc.)
    - Bold (**text**)
    - Italic (*text*)
    - Code (`code` and code blocks)
    - Links [text](url)
    - Blockquotes (> text)
    - Lists (-, *, 1.)
    - Protected region markers and content

    The highlighter re-applies when the protection manager signals
    that regions have changed.

    Example:
        >>> from PySide6.QtWidgets import QPlainTextEdit
        >>> editor = QPlainTextEdit()
        >>> highlighter = ProtectedMarkdownHighlighter(
        ...     editor.document(),
        ...     protection_manager,
        ... )
    """

    def __init__(
        self,
        document: QTextDocument,
        protection_manager: ProtectionManager | None = None,
        parent: QSyntaxHighlighter | None = None,
    ) -> None:
        """
        Initialize the highlighter.

        Args:
            document: The QTextDocument to highlight.
            protection_manager: Optional manager for protected regions.
            parent: Optional Qt parent.
        """
        super().__init__(parent or document)
        self.setDocument(document)
        self._protection_manager = protection_manager
        self._setup_formats()
        self._compile_patterns()

        # Connect to protection manager signals
        if protection_manager is not None:
            protection_manager.regions_changed.connect(self.rehighlight)

    def _setup_formats(self) -> None:
        """Set up text formats for different syntax elements."""
        # Header format
        self._header_format = QTextCharFormat()
        self._header_format.setForeground(QColor(get_header_color()))
        self._header_format.setFontWeight(QFont.Weight.Bold)

        # Bold format
        self._bold_format = QTextCharFormat()
        self._bold_format.setFontWeight(QFont.Weight.Bold)
        self._bold_format.setForeground(QColor(get_strong_color()))

        # Italic format
        self._italic_format = QTextCharFormat()
        self._italic_format.setFontItalic(True)
        self._italic_format.setForeground(QColor(get_emphasis_color()))

        # Code format
        self._code_format = QTextCharFormat()
        self._code_format.setFontFamily("Cascadia Code, Consolas, monospace")
        self._code_format.setBackground(QColor(get_code_background_color()))
        self._code_format.setForeground(QColor(get_code_text_color()))

        # Link format
        self._link_format = QTextCharFormat()
        self._link_format.setForeground(QColor(get_link_color()))
        self._link_format.setFontUnderline(True)

        # URL format (the URL part in [text](url))
        self._url_format = QTextCharFormat()
        self._url_format.setForeground(QColor(get_marker_color()))

        # Blockquote format
        self._blockquote_format = QTextCharFormat()
        self._blockquote_format.setForeground(QColor(get_blockquote_color()))
        self._blockquote_format.setFontItalic(True)

        # List marker format
        self._list_format = QTextCharFormat()
        self._list_format.setForeground(QColor(get_marker_color()))
        self._list_format.setFontWeight(QFont.Weight.Bold)

        # Protected region marker format
        self._protected_marker_format = QTextCharFormat()
        self._protected_marker_format.setForeground(QColor(get_protected_border_color()))
        self._protected_marker_format.setBackground(
            QColor(get_protected_background_color())
        )
        self._protected_marker_format.setFontItalic(True)

        # Protected content format
        self._protected_content_format = QTextCharFormat()
        self._protected_content_format.setBackground(
            QColor(get_protected_background_color())
        )
        self._protected_content_format.setForeground(
            QColor(get_protected_text_color())
        )

        # Strikethrough format
        self._strikethrough_format = QTextCharFormat()
        self._strikethrough_format.setFontStrikeOut(True)

    def _compile_patterns(self) -> None:
        """Compile regex patterns for syntax elements."""
        self._patterns: list[tuple[re.Pattern, QTextCharFormat, int]] = [
            # Headers: # Header
            (re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE), self._header_format, 0),
            # Bold: **text** or __text__
            (re.compile(r"\*\*([^*]+)\*\*"), self._bold_format, 1),
            (re.compile(r"__([^_]+)__"), self._bold_format, 1),
            # Italic: *text* or _text_
            (re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)"), self._italic_format, 1),
            (re.compile(r"(?<!_)_([^_]+)_(?!_)"), self._italic_format, 1),
            # Strikethrough: ~~text~~
            (re.compile(r"~~([^~]+)~~"), self._strikethrough_format, 1),
            # Inline code: `code`
            (re.compile(r"`([^`]+)`"), self._code_format, 0),
            # Links: [text](url)
            (re.compile(r"\[([^\]]+)\]"), self._link_format, 0),
            (re.compile(r"\]\(([^)]+)\)"), self._url_format, 0),
            # Blockquotes: > text
            (re.compile(r"^>\s+(.+)$", re.MULTILINE), self._blockquote_format, 0),
            # Unordered list items: - item, * item
            (re.compile(r"^(\s*[-*+])\s+", re.MULTILINE), self._list_format, 1),
            # Ordered list items: 1. item
            (re.compile(r"^(\s*\d+\.)\s+", re.MULTILINE), self._list_format, 1),
            # Protected region markers
            (
                re.compile(r"<!--\s*PROTECTED:\S+\s*-->"),
                self._protected_marker_format,
                0,
            ),
            (
                re.compile(r"<!--\s*/PROTECTED:\S+\s*-->"),
                self._protected_marker_format,
                0,
            ),
        ]

        # Code block pattern (```...```) - handled specially
        self._code_block_pattern = re.compile(r"```.*?```", re.DOTALL)

    def highlightBlock(self, text: str) -> None:
        """
        Apply syntax highlighting to a block of text.

        Args:
            text: The text of the current block to highlight.
        """
        # Apply pattern-based highlighting
        for pattern, fmt, group in self._patterns:
            for match in pattern.finditer(text):
                start = match.start(group) if group else match.start()
                length = match.end(group) - start if group else match.end() - match.start()
                self.setFormat(start, length, fmt)

        # Apply protected region content highlighting
        self._highlight_protected_content(text)

    def _highlight_protected_content(self, text: str) -> None:
        """
        Highlight content within protected regions.

        Args:
            text: The text of the current block.
        """
        if self._protection_manager is None:
            return

        block = self.currentBlock()
        block_start = block.position()
        block_end = block_start + len(text)

        for region in self._protection_manager.get_regions():
            # Check if this block overlaps with the region
            if block_start < region.end_offset and block_end > region.start_offset:
                # Calculate the overlap
                highlight_start = max(0, region.start_offset - block_start)
                highlight_end = min(len(text), region.end_offset - block_start)

                # Apply protected content format to the overlap
                if highlight_end > highlight_start:
                    # Don't override marker highlighting
                    for i in range(highlight_start, highlight_end):
                        current_format = self.format(i)
                        # Only apply if not already a marker
                        if current_format.background().color() != QColor(
                            get_protected_background_color()
                        ):
                            merged = QTextCharFormat(current_format)
                            merged.setBackground(
                                QColor(get_protected_background_color())
                            )
                            merged.setForeground(
                                QColor(get_protected_text_color())
                            )
                            self.setFormat(i, 1, merged)


class CodeBlockHighlighter(QSyntaxHighlighter):
    """
    Additional highlighter for multi-line code blocks.

    This is a simpler highlighter that only handles code blocks
    spanning multiple lines, which the main highlighter cannot
    handle easily.
    """

    def __init__(
        self,
        document: QTextDocument,
        parent: QSyntaxHighlighter | None = None,
    ) -> None:
        """
        Initialize the code block highlighter.

        Args:
            document: The QTextDocument to highlight.
            parent: Optional Qt parent.
        """
        super().__init__(parent or document)
        self.setDocument(document)
        self._setup_format()

    def _setup_format(self) -> None:
        """Set up the code block format."""
        self._code_block_format = QTextCharFormat()
        self._code_block_format.setFontFamily("Cascadia Code, Consolas, monospace")
        self._code_block_format.setBackground(QColor(get_code_background_color()))
        self._code_block_format.setForeground(QColor(get_code_text_color()))

    def highlightBlock(self, text: str) -> None:
        """
        Highlight code blocks.

        Uses block state to track multi-line code blocks.
        State 0: normal text
        State 1: inside code block
        """
        # Check if we're continuing a code block from previous block
        prev_state = self.previousBlockState()
        in_code_block = prev_state == 1

        # Look for code fence
        fence_pattern = re.compile(r"^```")

        if fence_pattern.match(text):
            # Toggle code block state
            if in_code_block:
                # Closing fence
                self.setFormat(0, len(text), self._code_block_format)
                self.setCurrentBlockState(0)
            else:
                # Opening fence
                self.setFormat(0, len(text), self._code_block_format)
                self.setCurrentBlockState(1)
        elif in_code_block:
            # Inside code block
            self.setFormat(0, len(text), self._code_block_format)
            self.setCurrentBlockState(1)
        else:
            self.setCurrentBlockState(0)
