"""
Protected content region management for the logbook widget.

Provides tracking and enforcement of protected content regions in markdown
documents using HTML comment markers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    pass


# Regex pattern for protected regions: <!-- PROTECTED:id -->...<!-- /PROTECTED:id -->
PROTECTED_PATTERN = re.compile(
    r"<!--\s*PROTECTED:(\S+)\s*-->(.*?)<!--\s*/PROTECTED:\1\s*-->",
    re.DOTALL,
)


@dataclass
class ProtectedRegion:
    """
    Represents a protected content region in a markdown document.

    Attributes:
        region_id: Unique identifier for this region (from the marker).
        start_offset: Character offset where the region starts (including marker).
        end_offset: Character offset where the region ends (including marker).
        content: The content between the markers (excluding markers).
        unlocked: Whether this region has been temporarily unlocked for editing.
    """

    region_id: str
    start_offset: int
    end_offset: int
    content: str
    unlocked: bool = field(default=False)

    @property
    def content_start(self) -> int:
        """Character offset where the protected content starts (after opening marker)."""
        # The opening marker is: <!-- PROTECTED:id -->
        marker_len = len(f"<!-- PROTECTED:{self.region_id} -->")
        return self.start_offset + marker_len

    @property
    def content_end(self) -> int:
        """Character offset where the protected content ends (before closing marker)."""
        # The closing marker is: <!-- /PROTECTED:id -->
        marker_len = len(f"<!-- /PROTECTED:{self.region_id} -->")
        return self.end_offset - marker_len


class ProtectionManager(QObject):
    """
    Manages protected content regions in a markdown document.

    This class parses markdown to identify protected regions marked with
    HTML comments, tracks their positions, and provides methods to check
    whether edits would affect protected content.

    Signals:
        protection_violated(str, int): Emitted when an edit is attempted in a
            protected region. Args are (region_id, cursor_position).
        region_unlocked(str): Emitted when a region is unlocked.
        region_locked(str): Emitted when a region is locked.
        regions_changed(): Emitted when regions are re-parsed.

    Example:
        >>> manager = ProtectionManager()
        >>> markdown = '''
        ... # Header
        ... <!-- PROTECTED:auto-data -->
        ... Generated: 2024-01-15
        ... <!-- /PROTECTED:auto-data -->
        ... '''
        >>> manager.parse_regions(markdown)
        >>> manager.is_position_protected(30)
        (True, 'auto-data')
    """

    # Signals
    protection_violated = Signal(str, int)  # (region_id, cursor_position)
    region_unlocked = Signal(str)  # region_id
    region_locked = Signal(str)  # region_id
    regions_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """
        Initialize the protection manager.

        Args:
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._regions: list[ProtectedRegion] = []

    def parse_regions(self, markdown: str) -> list[ProtectedRegion]:
        """
        Parse markdown content and extract protected regions.

        This method finds all protected region markers and creates
        ProtectedRegion objects with their positions.

        Args:
            markdown: The markdown content to parse.

        Returns:
            List of ProtectedRegion objects found in the content.
        """
        self._regions = []

        for match in PROTECTED_PATTERN.finditer(markdown):
            region_id = match.group(1)
            content = match.group(2)
            start_offset = match.start()
            end_offset = match.end()

            region = ProtectedRegion(
                region_id=region_id,
                start_offset=start_offset,
                end_offset=end_offset,
                content=content,
            )
            self._regions.append(region)
            logger.debug(
                f"Found protected region '{region_id}' at {start_offset}-{end_offset}"
            )

        self.regions_changed.emit()
        return self._regions

    def get_regions(self) -> list[ProtectedRegion]:
        """
        Get all currently tracked protected regions.

        Returns:
            List of ProtectedRegion objects.
        """
        return self._regions.copy()

    def get_region(self, region_id: str) -> ProtectedRegion | None:
        """
        Get a specific region by ID.

        Args:
            region_id: The ID of the region to find.

        Returns:
            The ProtectedRegion if found, None otherwise.
        """
        for region in self._regions:
            if region.region_id == region_id:
                return region
        return None

    def is_position_protected(self, position: int) -> tuple[bool, str | None]:
        """
        Check if a character position falls within a protected region.

        Args:
            position: The character offset to check.

        Returns:
            Tuple of (is_protected, region_id or None).
        """
        for region in self._regions:
            if region.unlocked:
                continue
            if region.start_offset <= position < region.end_offset:
                return True, region.region_id
        return False, None

    def is_range_protected(
        self, start: int, end: int
    ) -> tuple[bool, str | None]:
        """
        Check if a character range overlaps any protected region.

        Args:
            start: Start of the range (inclusive).
            end: End of the range (exclusive).

        Returns:
            Tuple of (is_protected, first_overlapping_region_id or None).
        """
        for region in self._regions:
            if region.unlocked:
                continue
            # Check for any overlap
            if start < region.end_offset and end > region.start_offset:
                return True, region.region_id
        return False, None

    def unlock_region(self, region_id: str) -> bool:
        """
        Temporarily unlock a protected region for editing.

        Args:
            region_id: The ID of the region to unlock.

        Returns:
            True if the region was found and unlocked, False otherwise.
        """
        region = self.get_region(region_id)
        if region is None:
            logger.warning(f"Cannot unlock unknown region: {region_id}")
            return False

        if not region.unlocked:
            region.unlocked = True
            logger.info(f"Unlocked protected region: {region_id}")
            self.region_unlocked.emit(region_id)
        return True

    def lock_region(self, region_id: str) -> bool:
        """
        Re-lock a previously unlocked region.

        Args:
            region_id: The ID of the region to lock.

        Returns:
            True if the region was found and locked, False otherwise.
        """
        region = self.get_region(region_id)
        if region is None:
            logger.warning(f"Cannot lock unknown region: {region_id}")
            return False

        if region.unlocked:
            region.unlocked = False
            logger.info(f"Locked protected region: {region_id}")
            self.region_locked.emit(region_id)
        return True

    def lock_all_regions(self) -> None:
        """Lock all currently unlocked regions."""
        for region in self._regions:
            if region.unlocked:
                region.unlocked = False
                self.region_locked.emit(region.region_id)

    def check_and_emit_violation(
        self, position: int, length: int = 1
    ) -> bool:
        """
        Check if an edit at the given position would violate protection,
        and emit the protection_violated signal if so.

        This is a convenience method that combines checking and signaling.

        Args:
            position: The position where the edit would occur.
            length: The length of the edit (for deletions/selections).

        Returns:
            True if a violation was detected, False otherwise.
        """
        is_protected, region_id = self.is_range_protected(
            position, position + length
        )
        if is_protected and region_id is not None:
            logger.debug(
                f"Protection violation in region '{region_id}' at position {position}"
            )
            self.protection_violated.emit(region_id, position)
            return True
        return False

    def update_region_offsets(self, position: int, delta: int) -> None:
        """
        Update region offsets after content has been modified.

        Call this after an edit has been made to keep region positions
        synchronized with the document content.

        Args:
            position: The position where the edit occurred.
            delta: The change in length (positive for insertions, negative for deletions).
        """
        for region in self._regions:
            if region.start_offset > position:
                # Region is entirely after the edit
                region.start_offset += delta
                region.end_offset += delta
            elif region.end_offset > position:
                # Edit is within or at the end of the region
                region.end_offset += delta
