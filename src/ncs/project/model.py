"""Data models for NCS projects and logbooks.

This module provides Pydantic models for:
- Project: Container for experiment work
- Logbook: Ordered collection of entries
- LogbookEntry: Individual log entries (notes, actions, scans, etc.)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EntryType(str, Enum):
    """Types of logbook entries."""

    NOTE = "note"  # User-written notes (editable)
    ACTION = "action"  # Device commands (move, set)
    SCAN = "scan"  # Scan start/progress/complete
    SNAPSHOT = "snapshot"  # Device state capture
    SYSTEM = "system"  # Session events, errors


class EntrySource(str, Enum):
    """Source of logbook entries."""

    USER = "user"  # User-created
    SYSTEM = "system"  # System-generated
    SCAN = "scan"  # Scan engine


class Attachment(BaseModel):
    """Attachment to a logbook entry."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    attachment_type: str  # "plot", "image", "data_ref", "snapshot"
    data: dict[str, Any] = Field(default_factory=dict)
    created: datetime = Field(default_factory=datetime.now)


class LogbookEntry(BaseModel):
    """A single entry in a logbook.

    Entries can be user notes (editable) or system-generated records
    (protected). Protected entries use HTML comment markers in the
    rendered markdown.

    Attributes:
        id: Unique identifier for this entry.
        timestamp: When the entry was created.
        entry_type: Type of entry (note, action, scan, etc.).
        source: Who/what created this entry.
        content: Markdown content of the entry.
        protected: Whether this entry is protected from editing.
        attachments: Associated plots, images, data references.
        metadata: Additional entry-specific data.
    """

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.now)
    entry_type: EntryType = EntryType.NOTE
    source: EntrySource = EntrySource.USER
    content: str = ""
    protected: bool = False
    attachments: list[Attachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_markdown(self) -> str:
        """Render this entry as markdown.

        Protected entries are wrapped in protection markers.

        Returns:
            Markdown string representation.
        """
        # Format timestamp
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        # Build header based on entry type
        type_icons = {
            EntryType.NOTE: "",
            EntryType.ACTION: "[Action]",
            EntryType.SCAN: "[Scan]",
            EntryType.SNAPSHOT: "[Snapshot]",
            EntryType.SYSTEM: "[System]",
        }
        icon = type_icons.get(self.entry_type, "")
        header = f"### {icon} {ts}".strip()

        # Build content
        lines = [header, "", self.content]

        # Add attachment references if any
        if self.attachments:
            lines.append("")
            for att in self.attachments:
                lines.append(f"- [{att.name}] ({att.attachment_type})")

        markdown = "\n".join(lines)

        # Wrap in protection markers if protected
        if self.protected:
            region_id = str(self.id)
            markdown = (
                f"<!-- PROTECTED:{region_id} -->\n"
                f"{markdown}\n"
                f"<!-- /PROTECTED:{region_id} -->"
            )

        return markdown


class Logbook(BaseModel):
    """A collection of logbook entries.

    Logbooks belong to a project and contain an ordered list of entries.
    The entries are rendered as a continuous markdown document.

    Attributes:
        id: Unique identifier for this logbook.
        title: Display title for the logbook.
        description: Optional description.
        created: When the logbook was created.
        modified: When the logbook was last modified.
        entries: Ordered list of entries.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str = "Logbook"
    description: str = ""
    created: datetime = Field(default_factory=datetime.now)
    modified: datetime = Field(default_factory=datetime.now)
    entries: list[LogbookEntry] = Field(default_factory=list)

    def add_entry(self, entry: LogbookEntry) -> None:
        """Add an entry to the logbook.

        Args:
            entry: The entry to add.
        """
        self.entries.append(entry)
        self.modified = datetime.now()

    def add_note(self, content: str) -> LogbookEntry:
        """Add a user note entry.

        Args:
            content: Markdown content for the note.

        Returns:
            The created entry.
        """
        entry = LogbookEntry(
            entry_type=EntryType.NOTE,
            source=EntrySource.USER,
            content=content,
            protected=False,
        )
        self.add_entry(entry)
        return entry

    def add_system_entry(
        self,
        content: str,
        entry_type: EntryType = EntryType.SYSTEM,
        metadata: dict[str, Any] | None = None,
    ) -> LogbookEntry:
        """Add a system-generated entry (protected).

        Args:
            content: Markdown content.
            entry_type: Type of system entry.
            metadata: Additional metadata.

        Returns:
            The created entry.
        """
        entry = LogbookEntry(
            entry_type=entry_type,
            source=EntrySource.SYSTEM,
            content=content,
            protected=True,
            metadata=metadata or {},
        )
        self.add_entry(entry)
        return entry

    def to_markdown(self) -> str:
        """Render the entire logbook as markdown.

        Returns:
            Complete markdown document with all entries.
        """
        parts = [f"# {self.title}", ""]

        if self.description:
            parts.extend([self.description, ""])

        parts.append("---")
        parts.append("")

        for entry in self.entries:
            parts.append(entry.to_markdown())
            parts.append("")

        return "\n".join(parts)

    def get_entry(self, entry_id: UUID) -> LogbookEntry | None:
        """Get an entry by ID.

        Args:
            entry_id: The entry ID to find.

        Returns:
            The entry or None if not found.
        """
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None


class ProjectMetadata(BaseModel):
    """Metadata for a project."""

    name: str
    description: str = ""
    created: datetime = Field(default_factory=datetime.now)
    modified: datetime = Field(default_factory=datetime.now)
    tags: list[str] = Field(default_factory=list)
    beamline: str | None = None
    esaf_id: str | None = None  # Experiment Safety Approval Form


class Project(BaseModel):
    """A project containing logbooks and experiment context.

    Projects are the top-level organizing unit in NCS. After login,
    users open a project which provides the context for their work.

    Attributes:
        id: Unique identifier for this project.
        metadata: Project metadata (name, description, etc.).
        logbooks: List of logbooks in this project.
        active_logbook_id: ID of the currently active logbook.
        settings: Project-specific configuration overrides.
    """

    id: UUID = Field(default_factory=uuid4)
    metadata: ProjectMetadata
    logbooks: list[Logbook] = Field(default_factory=list)
    active_logbook_id: UUID | None = None
    settings: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        """Get the project name."""
        return self.metadata.name

    @property
    def active_logbook(self) -> Logbook | None:
        """Get the currently active logbook."""
        if self.active_logbook_id is None:
            return self.logbooks[0] if self.logbooks else None
        for logbook in self.logbooks:
            if logbook.id == self.active_logbook_id:
                return logbook
        return None

    def create_logbook(self, title: str, description: str = "") -> Logbook:
        """Create a new logbook in this project.

        Args:
            title: Logbook title.
            description: Optional description.

        Returns:
            The created logbook.
        """
        logbook = Logbook(title=title, description=description)
        self.logbooks.append(logbook)
        if self.active_logbook_id is None:
            self.active_logbook_id = logbook.id
        self.metadata.modified = datetime.now()
        return logbook

    def set_active_logbook(self, logbook_id: UUID) -> bool:
        """Set the active logbook by ID.

        Args:
            logbook_id: ID of the logbook to activate.

        Returns:
            True if successful, False if logbook not found.
        """
        for logbook in self.logbooks:
            if logbook.id == logbook_id:
                self.active_logbook_id = logbook_id
                return True
        return False

    def get_logbook(self, logbook_id: UUID) -> Logbook | None:
        """Get a logbook by ID.

        Args:
            logbook_id: The logbook ID to find.

        Returns:
            The logbook or None if not found.
        """
        for logbook in self.logbooks:
            if logbook.id == logbook_id:
                return logbook
        return None
