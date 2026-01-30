"""
Block-based mapping between visual QTextDocument and markdown source.

This module provides stable position mapping by using QTextBlock structure
rather than absolute character positions. Block-relative offsets remain
stable even when content in earlier blocks changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtGui import QTextBlockUserData

if TYPE_CHECKING:
    from PySide6.QtGui import QTextDocument


# Pattern to match protected region markers
PROTECTED_START_PATTERN = re.compile(r"<!--\s*PROTECTED:(\S+)\s*-->")
PROTECTED_END_PATTERN = re.compile(r"<!--\s*/PROTECTED:(\S+)\s*-->")


@dataclass
class BlockMapping:
    """
    Maps a visual block to its corresponding markdown source location.

    Attributes:
        visual_block_num: The block number in the QTextDocument.
        md_line_start: Starting line number in markdown (0-indexed).
        md_char_start: Character offset where this block's content starts in markdown.
        md_char_end: Character offset where this block's content ends in markdown.
        is_protected: Whether this block is protected from editing.
        region_id: The protection region ID if protected, None otherwise.
        visual_text: The actual text content of the visual block.
        md_content_offset: Offset within the markdown line where visible content starts.
                          (e.g., after "# " for headers, after "- " for list items)
        visual_to_md_offsets: Character-level mapping from visual offset to markdown offset
                              within the content portion of the line. Accounts for inline
                              formatting markers like ** and *.
    """

    visual_block_num: int
    md_line_start: int
    md_char_start: int
    md_char_end: int
    is_protected: bool = False
    region_id: str | None = None
    visual_text: str = ""
    md_content_offset: int = 0  # Offset to skip markdown syntax
    visual_to_md_offsets: list[int] = field(default_factory=list)  # Visual pos -> MD offset within content


class BlockProtectionData(QTextBlockUserData):
    """
    User data attached to QTextBlocks for quick protection lookup.

    This allows O(1) protection checking by attaching metadata directly
    to each block in the QTextDocument.
    """

    def __init__(
        self,
        region_id: str | None = None,
        md_line_num: int = -1,
        is_protected: bool = False,
    ) -> None:
        super().__init__()
        self.region_id = region_id
        self.md_line_num = md_line_num
        self.is_protected = is_protected


class BlockMapper:
    """
    Maps between visual QTextDocument blocks and markdown source positions.

    This mapper builds a correspondence between visual blocks (paragraphs in
    the rendered QTextEdit) and lines/positions in the markdown source. It
    enables stable position tracking using (block_num, offset_in_block) tuples
    instead of absolute character positions.

    Example:
        >>> mapper = BlockMapper()
        >>> mapper.build_mappings(markdown, document)
        >>> md_pos = mapper.visual_to_md_pos(block_num=2, offset=5)
        >>> is_protected = mapper.is_block_protected(block_num=2)
    """

    def __init__(self) -> None:
        """Initialize the mapper with empty state."""
        self._mappings: list[BlockMapping] = []
        self._md_lines: list[str] = []
        self._md_line_offsets: list[int] = []  # Character offset of each line start

    @property
    def mappings(self) -> list[BlockMapping]:
        """Get all block mappings."""
        return self._mappings

    def build_mappings(
        self,
        markdown: str,
        document: QTextDocument,
    ) -> None:
        """
        Build block mappings between visual document and markdown source.

        This analyzes both the markdown source and the rendered QTextDocument
        to establish correspondences between visual blocks and markdown lines.

        Args:
            markdown: The markdown source text.
            document: The rendered QTextDocument from QTextEdit.
        """
        self._mappings.clear()

        # Parse markdown into lines with character offsets
        self._md_lines = markdown.split("\n")
        self._md_line_offsets = []
        offset = 0
        for line in self._md_lines:
            self._md_line_offsets.append(offset)
            offset += len(line) + 1  # +1 for newline

        # Find protected regions in markdown
        protected_regions = self._find_protected_regions(markdown)

        # Walk through visual blocks and match to markdown lines
        block = document.begin()
        md_line_idx = 0

        while block.isValid():
            block_num = block.blockNumber()
            block_text = block.text()

            # Find the corresponding markdown line
            mapping = self._find_markdown_match(
                block_num,
                block_text,
                md_line_idx,
                protected_regions,
            )

            if mapping:
                self._mappings.append(mapping)
                # Advance markdown line index past this match
                if mapping.md_line_start >= md_line_idx:
                    md_line_idx = mapping.md_line_start + 1

            block = block.next()

        logger.debug(f"Built {len(self._mappings)} block mappings")

    def _find_protected_regions(
        self, markdown: str
    ) -> dict[int, tuple[str, int, int]]:
        """
        Find all protected regions and their line numbers.

        Returns:
            Dict mapping line number to (region_id, start_line, end_line).
        """
        regions: dict[int, tuple[str, int, int]] = {}
        lines = markdown.split("\n")

        current_region: tuple[str, int] | None = None

        for line_num, line in enumerate(lines):
            start_match = PROTECTED_START_PATTERN.search(line)
            if start_match:
                region_id = start_match.group(1)
                current_region = (region_id, line_num)

            end_match = PROTECTED_END_PATTERN.search(line)
            if end_match and current_region:
                region_id, start_line = current_region
                # Mark all lines in the region
                for ln in range(start_line, line_num + 1):
                    regions[ln] = (region_id, start_line, line_num)
                current_region = None

        return regions

    def _find_markdown_match(
        self,
        block_num: int,
        block_text: str,
        start_line_idx: int,
        protected_regions: dict[int, tuple[str, int, int]],
    ) -> BlockMapping | None:
        """
        Find the markdown line that corresponds to a visual block.

        This uses text matching to find the corresponding markdown line,
        accounting for markdown syntax that's stripped in rendering.
        """
        # Clean the block text (remove zero-width markers if present)
        clean_text = block_text.replace("\u200b", "").replace("\u200c", "").strip()

        # Skip empty blocks - they might be spacing
        if not clean_text:
            # Map empty blocks to a synthetic entry
            if start_line_idx < len(self._md_lines):
                char_start = self._md_line_offsets[start_line_idx]
                return BlockMapping(
                    visual_block_num=block_num,
                    md_line_start=start_line_idx,
                    md_char_start=char_start,
                    md_char_end=char_start,
                    is_protected=False,
                    region_id=None,
                    visual_text=block_text,
                    md_content_offset=0,
                    visual_to_md_offsets=[0],
                )
            return None

        # Search for matching markdown line
        for line_idx in range(start_line_idx, len(self._md_lines)):
            md_line = self._md_lines[line_idx]

            # Skip lines that are purely HTML comments (no actual content)
            # But don't skip lines that have content alongside comments (compact protected regions)
            line_without_comments = re.sub(r"<!--.*?-->", "", md_line.strip())
            if not line_without_comments.strip():
                continue

            # Check if this line matches the visual text
            # Account for markdown syntax being stripped
            content_offset, matches, inline_offsets = self._line_matches_text(
                md_line, clean_text
            )

            if matches:
                char_start = self._md_line_offsets[line_idx]
                char_end = char_start + len(md_line)

                # Check if this line is in a protected region
                is_protected = line_idx in protected_regions
                region_id = protected_regions[line_idx][0] if is_protected else None

                return BlockMapping(
                    visual_block_num=block_num,
                    md_line_start=line_idx,
                    md_char_start=char_start,
                    md_char_end=char_end,
                    is_protected=is_protected,
                    region_id=region_id,
                    visual_text=block_text,
                    md_content_offset=content_offset,
                    visual_to_md_offsets=inline_offsets,
                )

        # No match found - create a fallback mapping
        logger.warning(f"No markdown match for block {block_num}: {clean_text[:30]}...")
        return None

    def _line_matches_text(
        self, md_line: str, visual_text: str
    ) -> tuple[int, bool, list[int]]:
        """
        Check if a markdown line matches visual text after syntax stripping.

        Returns:
            Tuple of (content_offset, matches, visual_to_md_offsets) where:
            - content_offset: position in md_line where the visible content starts
            - matches: whether this line matches the visual text
            - visual_to_md_offsets: mapping from visual char position to markdown offset
        """
        # Try various markdown patterns
        stripped = md_line.strip()

        # Strip HTML comments (protection markers)
        stripped_no_comments = re.sub(r"<!--.*?-->", "", stripped)

        # Header: # ## ### etc.
        header_match = re.match(r"^(#{1,6})\s+(.*)$", stripped_no_comments)
        if header_match:
            content = header_match.group(2)
            if content.strip() == visual_text:
                offset = md_line.find(content)
                offsets = self._build_inline_offset_map(content, visual_text)
                return (offset, True, offsets)

        # List item: - * or 1.
        list_match = re.match(r"^([-*]|\d+\.)\s+(.*)$", stripped_no_comments)
        if list_match:
            content = list_match.group(2)
            if content.strip() == visual_text:
                offset = md_line.find(content)
                offsets = self._build_inline_offset_map(content, visual_text)
                return (offset, True, offsets)

        # Bold/italic: **text** *text* etc.
        # Strip formatting markers for comparison
        plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped_no_comments)
        plain = re.sub(r"\*([^*]+)\*", r"\1", plain)
        plain = re.sub(r"__([^_]+)__", r"\1", plain)
        plain = re.sub(r"_([^_]+)_", r"\1", plain)
        plain = re.sub(r"~~([^~]+)~~", r"\1", plain)

        if plain.strip() == visual_text:
            # Find where content starts (skip leading whitespace)
            offset = len(md_line) - len(md_line.lstrip())
            # Build offset map from the content portion (after leading whitespace)
            content = md_line.lstrip()
            # Also strip trailing whitespace from content for matching
            content = re.sub(r"<!--.*?-->", "", content)
            offsets = self._build_inline_offset_map(content, visual_text)
            return (offset, True, offsets)

        # Direct match (with comments stripped)
        if stripped_no_comments.strip() == visual_text:
            offset = len(md_line) - len(md_line.lstrip())
            offsets = self._build_inline_offset_map(stripped_no_comments, visual_text)
            return (offset, True, offsets)

        # Partial match (visual text is contained in markdown line)
        if visual_text in stripped_no_comments:
            offset = md_line.find(visual_text)
            # For partial matches, build a simple 1:1 mapping
            offsets = list(range(len(visual_text) + 1))
            return (offset, True, offsets)

        # Table cell match: visual text is a table cell value
        # Table lines look like: | Value1 | Value2 |
        if stripped_no_comments.startswith("|") and visual_text in stripped_no_comments:
            # Find the cell containing this text
            offset = md_line.find(visual_text)
            offsets = list(range(len(visual_text) + 1))
            return (offset, True, offsets)

        # Visual text starts with markdown content (prefix match)
        # Useful for merged lines like "Date: ... Operator: ..."
        if visual_text.startswith(plain.strip()[:20]) and len(plain.strip()) > 10:
            offset = len(md_line) - len(md_line.lstrip())
            content = md_line.lstrip()
            content = re.sub(r"<!--.*?-->", "", content)
            offsets = self._build_inline_offset_map(content, visual_text)
            return (offset, True, offsets)

        return (0, False, [])

    def _build_inline_offset_map(
        self, md_content: str, visual_text: str
    ) -> list[int]:
        """
        Build a character-level mapping from visual positions to markdown offsets.

        This accounts for inline formatting markers like **, *, ~~, etc. that are
        present in markdown but not in the visual text.

        Args:
            md_content: The markdown content (after line-level prefix like # or -)
            visual_text: The visual text (with formatting stripped)

        Returns:
            List where index is visual position, value is markdown offset within md_content.
            Length is len(visual_text) + 1 to include end position.
        """
        # Clean visual text of zero-width markers for mapping
        clean_visual = visual_text.replace("\u200b", "").replace("\u200c", "")

        # Build mapping by walking through both strings
        visual_to_md: list[int] = []
        md_pos = 0

        # Formatting markers to skip (order matters - ** before *, ~~ before ~)
        format_markers = ["**", "*", "__", "_", "~~", "`"]

        def skip_markers() -> None:
            """Skip any formatting markers or comments at current md_pos."""
            nonlocal md_pos
            skipped = True
            while skipped and md_pos < len(md_content):
                skipped = False

                # Skip HTML comments
                if md_content[md_pos:].startswith("<!--"):
                    end = md_content.find("-->", md_pos)
                    if end != -1:
                        md_pos = end + 3
                        skipped = True
                        continue

                # Check for format markers
                for marker in format_markers:
                    if md_content[md_pos:].startswith(marker):
                        md_pos += len(marker)
                        skipped = True
                        break

        # Build mapping for each visual position
        for _v_pos, v_char in enumerate(clean_visual):
            # Skip any markers before this character
            skip_markers()

            # Record mapping: visual pos v_pos maps to current md_pos
            visual_to_md.append(md_pos)

            # Find the matching character in markdown
            if md_pos < len(md_content):
                md_char = md_content[md_pos]
                if md_char == v_char:
                    md_pos += 1
                else:
                    # Mismatch - try to find the character
                    found = False
                    for skip in range(1, min(10, len(md_content) - md_pos)):
                        if md_content[md_pos + skip] == v_char:
                            md_pos += skip + 1
                            found = True
                            break
                    if not found:
                        # Give up and advance anyway
                        md_pos += 1

        # Skip any trailing markers and record end position
        skip_markers()
        visual_to_md.append(md_pos)

        return visual_to_md

    def visual_to_md_pos(self, block_num: int, offset_in_block: int) -> int:
        """
        Convert a visual (block, offset) position to markdown character position.

        Uses character-level inline offset mapping to account for formatting
        markers like ** and * that are present in markdown but not in visual.

        Args:
            block_num: The visual block number.
            offset_in_block: Character offset within the block.

        Returns:
            Absolute character position in the markdown source.
        """
        mapping = self.get_block_mapping(block_num)

        if mapping is None:
            # No mapping for this block - try to find nearest mapped block
            # and use its position as a fallback
            for b in range(block_num - 1, -1, -1):
                m = self.get_block_mapping(b)
                if m is not None:
                    # Return end of previous mapped block
                    return m.md_char_end
            return 0

        if mapping.is_protected:
            # For protected blocks, don't allow position calculation
            # Return the start of the protected content
            return mapping.md_char_start + mapping.md_content_offset

        # Use character-level inline offset mapping if available
        if mapping.visual_to_md_offsets:
            # The visual text may have zero-width markers that we need to account for
            # Strip them to get the clean offset
            visual_text = mapping.visual_text
            clean_offset = offset_in_block

            # Count zero-width markers before this position
            markers_before = 0
            for i, char in enumerate(visual_text):
                if i >= offset_in_block:
                    break
                if char in ("\u200b", "\u200c"):
                    markers_before += 1

            # Adjust offset to account for stripped markers
            clean_offset = offset_in_block - markers_before

            # Look up the inline offset
            if clean_offset < len(mapping.visual_to_md_offsets):
                inline_offset = mapping.visual_to_md_offsets[clean_offset]
            elif mapping.visual_to_md_offsets:
                # Past end - use last mapped position
                inline_offset = mapping.visual_to_md_offsets[-1]
            else:
                inline_offset = clean_offset

            md_pos = mapping.md_char_start + mapping.md_content_offset + inline_offset
        else:
            # Fallback: direct offset (no inline formatting)
            md_pos = mapping.md_char_start + mapping.md_content_offset + offset_in_block

        # Clamp to line bounds
        md_pos = min(md_pos, mapping.md_char_end)

        return md_pos

    def is_block_protected(self, block_num: int) -> tuple[bool, str | None]:
        """
        Check if a visual block is protected.

        Uses heuristic: if a block doesn't have a mapping but is between
        protected blocks, it's likely part of the protected region (e.g.,
        table cells within a protected table).

        Args:
            block_num: The visual block number.

        Returns:
            Tuple of (is_protected, region_id or None).
        """
        # Find the mapping for this block
        mapping = self.get_block_mapping(block_num)

        if mapping is not None:
            return mapping.is_protected, mapping.region_id

        # Block has no direct mapping - check if it's between protected blocks
        # This handles table cells and other complex rendered structures
        prev_protected = None
        next_protected = None

        # Look backwards for a protected block
        for b in range(block_num - 1, -1, -1):
            m = self.get_block_mapping(b)
            if m is not None:
                if m.is_protected:
                    prev_protected = m.region_id
                break

        # Look forwards for a protected block with the same region_id
        for b in range(block_num + 1, len(self._mappings) + 10):  # +10 for unmapped blocks
            m = self.get_block_mapping(b)
            if m is not None:
                if m.is_protected and m.region_id == prev_protected:
                    next_protected = m.region_id
                break

        # If sandwiched between same protected region, consider this block protected
        if prev_protected and next_protected and prev_protected == next_protected:
            return True, prev_protected

        return False, None

    def get_block_mapping(self, block_num: int) -> BlockMapping | None:
        """Get the mapping for a specific block by its block number."""
        for mapping in self._mappings:
            if mapping.visual_block_num == block_num:
                return mapping
        return None

    def debug_dump(self) -> str:
        """Generate a debug representation of all mappings."""
        lines = ["Block Mappings:"]
        for m in self._mappings:
            prot = f" [PROTECTED:{m.region_id}]" if m.is_protected else ""
            lines.append(
                f"  Block {m.visual_block_num}: md_line={m.md_line_start}, "
                f"chars={m.md_char_start}-{m.md_char_end}, "
                f"offset={m.md_content_offset}{prot}"
            )
            lines.append(f"    visual: {m.visual_text[:40]}...")
        return "\n".join(lines)
