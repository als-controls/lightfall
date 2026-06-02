"""
Visual protection tracking using zero-width Unicode boundary markers.

This module provides character-level protected region tracking for the
visual (WYSIWYG) editor using invisible zero-width Unicode characters
as boundary markers. This approach is more reliable than block-level
tracking because markers survive HTML rendering and provide exact
position information.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from lightfall.logbook.protection import ProtectedRegion

# Zero-width Unicode markers
# These characters are invisible but take up a position in the text
PROTECTED_START = "\u200B"  # Zero Width Space - marks start of protected region
PROTECTED_END = "\u200C"  # Zero Width Non-Joiner - marks end of protected region


@dataclass
class VisualProtectedRegion:
    """
    A protected region tracked by its visual text positions.

    Attributes:
        region_id: Unique identifier matching the markdown region.
        start_pos: Character position of start marker in visual text.
        end_pos: Character position of end marker in visual text.
        unlocked: Whether this region has been temporarily unlocked.
    """

    region_id: str
    start_pos: int  # Position of the start marker character
    end_pos: int  # Position of the end marker character
    unlocked: bool = False

    @property
    def content_start(self) -> int:
        """Position after the start marker (first protected character)."""
        return self.start_pos + 1

    @property
    def content_end(self) -> int:
        """Position of the end marker (first non-protected character after content)."""
        return self.end_pos


class VisualProtectionTracker:
    """
    Tracks protected regions in visual text using zero-width markers.

    This tracker maintains the positions of protected region boundaries
    in the visual/rendered text. It provides efficient O(log n) lookup
    for protection checking and handles position updates after edits.

    The workflow is:
    1. Markers are injected into HTML during markdown conversion
    2. After rendering, rebuild_from_text() scans for marker positions
    3. Protection checks use is_position_protected() / is_range_protected()
    4. After edits, positions are updated or tracker is rebuilt

    Example:
        >>> tracker = VisualProtectionTracker()
        >>> # After rendering markdown with protected regions:
        >>> tracker.rebuild_from_text(visual_text, protection_manager.get_regions())
        >>> is_protected, region_id = tracker.is_position_protected(cursor_pos)
    """

    def __init__(self) -> None:
        """Initialize the tracker with empty state."""
        self._regions: list[VisualProtectedRegion] = []
        # Sorted list of start positions for binary search
        self._start_positions: list[int] = []

    def clear(self) -> None:
        """Clear all tracked regions."""
        self._regions.clear()
        self._start_positions.clear()

    def rebuild_from_text(
        self,
        text: str,
        md_regions: list[ProtectedRegion],
    ) -> None:
        """
        Scan visual text and locate all zero-width boundary markers.

        This method finds all PROTECTED_START and PROTECTED_END markers
        in the text and matches them to the corresponding markdown regions.
        Regions are matched by order of appearance.

        Args:
            text: The visual/plain text from the editor (with markers).
            md_regions: The protected regions from ProtectionManager.
        """
        self.clear()

        # Find all marker positions
        start_positions: list[int] = []
        end_positions: list[int] = []

        pos = 0
        while pos < len(text):
            char = text[pos]
            if char == PROTECTED_START:
                start_positions.append(pos)
            elif char == PROTECTED_END:
                end_positions.append(pos)
            pos += 1

        # Match markers to regions by order
        # Each region should have exactly one start and one end marker
        num_regions = min(len(start_positions), len(end_positions), len(md_regions))

        if len(start_positions) != len(md_regions):
            logger.warning(
                f"Marker count mismatch: {len(start_positions)} starts, "
                f"{len(end_positions)} ends, {len(md_regions)} regions"
            )

        for i in range(num_regions):
            region = VisualProtectedRegion(
                region_id=md_regions[i].region_id,
                start_pos=start_positions[i],
                end_pos=end_positions[i],
                unlocked=md_regions[i].unlocked,
            )
            self._regions.append(region)
            self._start_positions.append(region.start_pos)

        logger.debug(f"Rebuilt visual tracker with {len(self._regions)} regions")

    def get_regions(self) -> list[VisualProtectedRegion]:
        """Get all tracked regions."""
        return self._regions.copy()

    def get_region(self, region_id: str) -> VisualProtectedRegion | None:
        """Get a region by ID."""
        for region in self._regions:
            if region.region_id == region_id:
                return region
        return None

    def is_position_protected(self, pos: int) -> tuple[bool, str | None]:
        """
        Check if a character position is within a protected region.

        A position is protected if it is:
        - At or after the start marker (start_pos)
        - Before the end marker (end_pos)

        This means the start marker position IS protected, but the
        end marker position is NOT protected (user can type there).

        Args:
            pos: Character position in visual text.

        Returns:
            Tuple of (is_protected, region_id or None).
        """
        # Binary search to find candidate region
        # Find the rightmost region whose start_pos <= pos
        idx = bisect.bisect_right(self._start_positions, pos) - 1

        if idx < 0:
            return False, None

        region = self._regions[idx]

        # Check if position is within this region
        if region.start_pos <= pos < region.end_pos:
            if region.unlocked:
                return False, None
            return True, region.region_id

        return False, None

    def is_range_protected(
        self, start: int, end: int
    ) -> tuple[bool, str | None]:
        """
        Check if a range overlaps any protected region.

        Args:
            start: Start of range (inclusive).
            end: End of range (exclusive).

        Returns:
            Tuple of (is_protected, first_overlapping_region_id or None).
        """
        for region in self._regions:
            if region.unlocked:
                continue
            # Check for overlap: ranges overlap if start < region.end AND end > region.start
            if start < region.end_pos and end > region.start_pos:
                return True, region.region_id

        return False, None

    def update_after_edit(self, pos: int, delta: int) -> None:
        """
        Update region positions after an edit operation.

        This should be called after any edit that changes the text length.
        All region boundaries after the edit position are shifted.

        Args:
            pos: Position where the edit occurred.
            delta: Change in length (positive for insert, negative for delete).
        """
        for region in self._regions:
            # If edit is before or at region start, shift both boundaries
            if region.start_pos >= pos:
                region.start_pos += delta
                region.end_pos += delta
            # If edit is within region (after start but before end), only shift end
            elif region.end_pos > pos:
                region.end_pos += delta

        # Rebuild the sorted start positions list
        self._start_positions = [r.start_pos for r in self._regions]

    def sync_unlock_state(self, md_regions: list[ProtectedRegion]) -> None:
        """
        Synchronize unlock state from markdown regions.

        Call this when a region is locked/unlocked to update the
        visual tracker's unlock state without rebuilding.

        Args:
            md_regions: The protected regions from ProtectionManager.
        """
        md_unlock_map = {r.region_id: r.unlocked for r in md_regions}
        for region in self._regions:
            if region.region_id in md_unlock_map:
                region.unlocked = md_unlock_map[region.region_id]

    @staticmethod
    def strip_markers(text: str) -> str:
        """
        Remove all zero-width protection markers from text.

        Use this to sanitize pasted text that might contain markers.

        Args:
            text: Text that may contain markers.

        Returns:
            Text with all PROTECTED_START and PROTECTED_END characters removed.
        """
        return text.replace(PROTECTED_START, "").replace(PROTECTED_END, "")

    @staticmethod
    def inject_markers_around_content(
        content: str,
    ) -> str:
        """
        Wrap content with protection markers.

        Args:
            content: The content to protect.

        Returns:
            Content wrapped with start and end markers.
        """
        return f"{PROTECTED_START}{content}{PROTECTED_END}"


def create_marker_injected_html(
    html: str,
    md_regions: list[ProtectedRegion],
    markdown: str,
) -> str:
    """
    Inject zero-width markers into HTML at protected region boundaries.

    This function processes rendered HTML and inserts PROTECTED_START
    and PROTECTED_END markers at positions corresponding to protected
    regions in the markdown source.

    The approach:
    1. Find protected spans in HTML (class="protected")
    2. Insert start marker after opening tag
    3. Insert end marker before closing tag

    Args:
        html: The rendered HTML from markdown conversion.
        md_regions: Protected regions from ProtectionManager.
        markdown: Original markdown source (for reference).

    Returns:
        HTML with zero-width markers injected.
    """
    if not md_regions:
        return html

    # The converter adds spans with class="protected" and data-region attribute
    # We need to inject markers inside these spans
    import re

    result = html

    for region in md_regions:
        # Pattern to find the protected span for this region
        # The span has: class="protected" data-region="region_id"
        pattern = (
            rf'(<span[^>]*class="[^"]*protected[^"]*"[^>]*'
            rf'data-region="{re.escape(region.region_id)}"[^>]*>)'
            rf'(.*?)'
            rf'(</span>)'
        )

        def inject_markers(match: re.Match) -> str:
            opening = match.group(1)
            content = match.group(2)
            closing = match.group(3)
            # Insert start marker after opening tag, end marker before closing tag
            return f"{opening}{PROTECTED_START}{content}{PROTECTED_END}{closing}"

        result = re.sub(pattern, inject_markers, result, flags=re.DOTALL)

    return result
