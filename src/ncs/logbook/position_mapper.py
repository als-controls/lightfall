"""
Position mapping between markdown source and visual (plain text) positions.

This module provides bidirectional mapping between positions in markdown
source text and the corresponding positions in rendered plain text output.
This enables visual editing where user actions in the rendered view are
translated to operations on the underlying markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar


@dataclass
class PositionMapping:
    """Stores the bidirectional position mapping."""

    # visual_to_md[visual_pos] = markdown_pos
    visual_to_md: list[int]
    # md_to_visual[md_pos] = visual_pos (or -1 if position is in syntax)
    md_to_visual: list[int]
    # The extracted plain text (what visual mode shows)
    plain_text: str


class MarkdownPositionMapper:
    """
    Maps positions between markdown source and visual plain text.

    The mapper walks through markdown text and builds a mapping between
    positions in the rendered plain text and positions in the source.
    This allows translating user cursor positions and selections from
    the visual editor to the corresponding markdown positions.

    Example:
        >>> mapper = MarkdownPositionMapper()
        >>> mapping = mapper.build_map("Hello **world**")
        >>> mapping.plain_text
        'Hello world'
        >>> mapping.visual_to_md[6]  # Position of 'w' in plain text
        8  # Position after '**' in markdown

    Supported markdown syntax:
        - Bold: **text** or __text__
        - Italic: *text* or _text_
        - Bold+Italic: ***text***
        - Strikethrough: ~~text~~
        - Inline code: `code`
        - Headers: # ## ### etc.
        - Links: [text](url)
        - Images: ![alt](url)
    """

    # Patterns for markdown syntax we need to handle
    # Order matters - check longer patterns first
    SYNTAX_PATTERNS: ClassVar[list[tuple[str, re.Pattern]]] = [
        ("bold_italic", re.compile(r"\*\*\*(.+?)\*\*\*", re.DOTALL)),
        ("bold_asterisk", re.compile(r"\*\*(.+?)\*\*", re.DOTALL)),
        ("bold_underscore", re.compile(r"__(.+?)__", re.DOTALL)),
        ("italic_asterisk", re.compile(r"\*(.+?)\*", re.DOTALL)),
        ("italic_underscore", re.compile(r"_(.+?)_", re.DOTALL)),
        ("strikethrough", re.compile(r"~~(.+?)~~", re.DOTALL)),
        ("code", re.compile(r"`([^`]+)`")),
        ("link", re.compile(r"\[([^\]]+)\]\([^)]+\)")),
        ("image", re.compile(r"!\[([^\]]*)\]\([^)]+\)")),
    ]

    def build_map(self, markdown: str) -> PositionMapping:
        """
        Build position mapping from markdown source.

        Args:
            markdown: The markdown source text.

        Returns:
            PositionMapping with bidirectional maps and plain text.
        """
        # visual_to_md[v] = m means visual position v corresponds to markdown position m
        visual_to_md: list[int] = []
        # md_to_visual[m] = v means markdown position m corresponds to visual position v
        # -1 means the position is inside syntax (not visible)
        md_to_visual: list[int] = [-1] * (len(markdown) + 1)

        plain_chars: list[str] = []

        md_pos = 0
        visual_pos = 0

        while md_pos < len(markdown):
            # Check for heading at start of line
            if md_pos == 0 or (md_pos > 0 and markdown[md_pos - 1] == "\n"):
                heading_match = re.match(r"^(#{1,6})\s+", markdown[md_pos:])
                if heading_match:
                    # Skip the heading syntax
                    syntax_len = len(heading_match.group(0))
                    md_pos += syntax_len
                    continue

            # Check for markdown syntax patterns
            matched = False
            for name, pattern in self.SYNTAX_PATTERNS:
                match = pattern.match(markdown, md_pos)
                if match:
                    matched = True
                    full_match = match.group(0)
                    content = match.group(1)

                    if name == "link":
                        # For links, we show the link text
                        # [text](url) - skip [ show text skip ](url)
                        # Position of [
                        md_to_visual[md_pos] = -1
                        md_pos += 1  # skip [

                        # Map the link text content
                        for char in content:
                            visual_to_md.append(md_pos)
                            md_to_visual[md_pos] = visual_pos
                            plain_chars.append(char)
                            md_pos += 1
                            visual_pos += 1

                        # Skip ](url)
                        remaining = len(full_match) - 1 - len(content)
                        for _ in range(remaining):
                            md_to_visual[md_pos] = -1
                            md_pos += 1

                    elif name == "image":
                        # For images, show alt text
                        # ![alt](url) - skip ![ show alt skip ](url)
                        md_to_visual[md_pos] = -1
                        md_pos += 1  # skip !
                        md_to_visual[md_pos] = -1
                        md_pos += 1  # skip [

                        # Map the alt text
                        for char in content:
                            visual_to_md.append(md_pos)
                            md_to_visual[md_pos] = visual_pos
                            plain_chars.append(char)
                            md_pos += 1
                            visual_pos += 1

                        # Skip ](url)
                        remaining = len(full_match) - 2 - len(content)
                        for _ in range(remaining):
                            md_to_visual[md_pos] = -1
                            md_pos += 1

                    else:
                        # For formatting (bold, italic, etc.)
                        # Calculate syntax lengths
                        content_start = match.start(1) - match.start(0)
                        content_end = content_start + len(content)
                        prefix_len = content_start
                        suffix_len = len(full_match) - content_end

                        # Skip opening syntax
                        for _ in range(prefix_len):
                            md_to_visual[md_pos] = -1
                            md_pos += 1

                        # Map content characters
                        for char in content:
                            visual_to_md.append(md_pos)
                            md_to_visual[md_pos] = visual_pos
                            plain_chars.append(char)
                            md_pos += 1
                            visual_pos += 1

                        # Skip closing syntax
                        for _ in range(suffix_len):
                            md_to_visual[md_pos] = -1
                            md_pos += 1

                    break

            if not matched:
                # Regular character - direct mapping
                char = markdown[md_pos]
                visual_to_md.append(md_pos)
                md_to_visual[md_pos] = visual_pos
                plain_chars.append(char)
                md_pos += 1
                visual_pos += 1

        # Add end-of-string mapping
        visual_to_md.append(md_pos)
        md_to_visual[md_pos] = visual_pos

        return PositionMapping(
            visual_to_md=visual_to_md,
            md_to_visual=md_to_visual,
            plain_text="".join(plain_chars),
        )

    def visual_to_markdown(self, mapping: PositionMapping, visual_pos: int) -> int:
        """
        Convert a visual position to markdown position.

        Args:
            mapping: The position mapping from build_map().
            visual_pos: Position in the visual/plain text.

        Returns:
            Corresponding position in the markdown source.
        """
        if visual_pos < 0:
            return 0
        if visual_pos >= len(mapping.visual_to_md):
            return mapping.visual_to_md[-1] if mapping.visual_to_md else 0
        return mapping.visual_to_md[visual_pos]

    def markdown_to_visual(self, mapping: PositionMapping, md_pos: int) -> int:
        """
        Convert a markdown position to visual position.

        Args:
            mapping: The position mapping from build_map().
            md_pos: Position in the markdown source.

        Returns:
            Corresponding position in the visual/plain text,
            or -1 if the position is inside syntax.
        """
        if md_pos < 0:
            return 0
        if md_pos >= len(mapping.md_to_visual):
            return len(mapping.plain_text)
        return mapping.md_to_visual[md_pos]

    def apply_insert(
        self, markdown: str, visual_pos: int, text: str, mapping: PositionMapping | None = None
    ) -> tuple[str, int]:
        """
        Insert text at a visual position, returning updated markdown.

        Args:
            markdown: Current markdown source.
            visual_pos: Position in visual view to insert at.
            text: Text to insert.
            mapping: Optional pre-built mapping (will build if not provided).

        Returns:
            Tuple of (new_markdown, new_visual_cursor_pos).
        """
        if mapping is None:
            mapping = self.build_map(markdown)

        md_pos = self.visual_to_markdown(mapping, visual_pos)
        new_markdown = markdown[:md_pos] + text + markdown[md_pos:]
        new_visual_pos = visual_pos + len(text)

        return new_markdown, new_visual_pos

    def apply_delete(
        self,
        markdown: str,
        visual_start: int,
        visual_end: int,
        mapping: PositionMapping | None = None,
    ) -> tuple[str, int]:
        """
        Delete text between visual positions, returning updated markdown.

        Args:
            markdown: Current markdown source.
            visual_start: Start position in visual view.
            visual_end: End position in visual view.
            mapping: Optional pre-built mapping (will build if not provided).

        Returns:
            Tuple of (new_markdown, new_visual_cursor_pos).
        """
        if mapping is None:
            mapping = self.build_map(markdown)

        md_start = self.visual_to_markdown(mapping, visual_start)
        md_end = self.visual_to_markdown(mapping, visual_end)

        new_markdown = markdown[:md_start] + markdown[md_end:]
        return new_markdown, visual_start

    def apply_formatting(
        self,
        markdown: str,
        visual_start: int,
        visual_end: int,
        prefix: str,
        suffix: str,
        mapping: PositionMapping | None = None,
    ) -> str:
        """
        Apply formatting (wrap with prefix/suffix) to a visual selection.

        Args:
            markdown: Current markdown source.
            visual_start: Start of selection in visual view.
            visual_end: End of selection in visual view.
            prefix: Markdown prefix (e.g., "**" for bold).
            suffix: Markdown suffix (e.g., "**" for bold).
            mapping: Optional pre-built mapping.

        Returns:
            Updated markdown with formatting applied.
        """
        if mapping is None:
            mapping = self.build_map(markdown)

        md_start = self.visual_to_markdown(mapping, visual_start)
        md_end = self.visual_to_markdown(mapping, visual_end)

        new_markdown = (
            markdown[:md_start] + prefix + markdown[md_start:md_end] + suffix + markdown[md_end:]
        )
        return new_markdown
