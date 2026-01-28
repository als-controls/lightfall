"""Project service for managing the active project.

This module provides ProjectService, a singleton that:
- Manages the currently active project
- Provides signals for project state changes
- Handles project creation, opening, and closing
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lucid.project.model import (
    EntrySource,
    EntryType,
    Logbook,
    LogbookEntry,
    Project,
    ProjectMetadata,
)

if TYPE_CHECKING:
    from typing import Any


class ProjectService(QObject):
    """Singleton service for managing the active project.

    ProjectService provides centralized project management with:
    - Active project tracking
    - Project lifecycle (create, open, close)
    - Logbook entry creation helpers
    - Qt signals for state changes

    Signals:
        project_opened: Emitted when a project is opened.
        project_closed: Emitted when a project is closed.
        active_logbook_changed: Emitted when active logbook changes.
        entry_added: Emitted when an entry is added to active logbook.

    Example:
        >>> service = ProjectService.get_instance()
        >>> project = service.create_project("My Experiment")
        >>> service.add_note("Starting experiment...")
    """

    _instance: ClassVar[ProjectService | None] = None

    # Signals
    project_opened = Signal(object)  # Project
    project_closed = Signal()
    active_logbook_changed = Signal(object)  # Logbook
    active_entry_changed = Signal(object)  # LogbookEntry | None
    entry_added = Signal(object)  # LogbookEntry

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the project service.

        Args:
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._active_project: Project | None = None
        self._recent_projects: list[dict[str, Any]] = []

    @classmethod
    def get_instance(cls) -> ProjectService:
        """Get the singleton instance.

        Returns:
            The ProjectService singleton.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None

    # === Properties ===

    @property
    def active_project(self) -> Project | None:
        """Get the currently active project."""
        return self._active_project

    @property
    def active_logbook(self) -> Logbook | None:
        """Get the active logbook from the active project."""
        if self._active_project is None:
            return None
        return self._active_project.active_logbook

    @property
    def has_project(self) -> bool:
        """Check if a project is currently open."""
        return self._active_project is not None

    @property
    def active_entry(self) -> LogbookEntry | None:
        """Get the active entry from the active logbook."""
        logbook = self.active_logbook
        if logbook is None:
            return None
        return logbook.active_entry

    @property
    def recent_projects(self) -> list[dict[str, Any]]:
        """Get list of recent projects.

        Returns:
            List of dicts with 'name', 'path', 'last_opened' keys.
        """
        return self._recent_projects.copy()

    # === Project Lifecycle ===

    def create_project(
        self,
        name: str,
        description: str = "",
        beamline: str | None = None,
    ) -> Project:
        """Create and open a new project.

        Args:
            name: Project name.
            description: Optional description.
            beamline: Optional beamline identifier.

        Returns:
            The created project.
        """
        # Close existing project if any
        if self._active_project is not None:
            self.close_project()

        # Create project with metadata
        metadata = ProjectMetadata(
            name=name,
            description=description,
            beamline=beamline,
        )
        project = Project(metadata=metadata)

        # Create default logbook
        project.create_logbook(
            title=f"{name} Log",
            description=f"Experiment logbook for {name}",
        )

        # Set as active
        self._active_project = project

        logger.info("Created project: {}", name)
        self.project_opened.emit(project)

        return project

    def open_project(self, project: Project) -> None:
        """Open an existing project.

        Args:
            project: The project to open.
        """
        # Close existing project if any
        if self._active_project is not None:
            self.close_project()

        self._active_project = project

        logger.info("Opened project: {}", project.name)
        self.project_opened.emit(project)

    def close_project(self) -> None:
        """Close the current project."""
        if self._active_project is None:
            return

        name = self._active_project.name
        self._active_project = None

        logger.info("Closed project: {}", name)
        self.project_closed.emit()

    # === Logbook Management ===

    def set_active_logbook(self, logbook_id: UUID) -> bool:
        """Set the active logbook in the current project.

        Args:
            logbook_id: ID of the logbook to activate.

        Returns:
            True if successful.
        """
        if self._active_project is None:
            return False

        if self._active_project.set_active_logbook(logbook_id):
            logbook = self._active_project.active_logbook
            logger.debug("Active logbook changed: {}", logbook.title if logbook else None)
            self.active_logbook_changed.emit(logbook)
            return True
        return False

    def create_logbook(self, title: str, description: str = "") -> Logbook | None:
        """Create a new logbook in the active project.

        Args:
            title: Logbook title.
            description: Optional description.

        Returns:
            The created logbook or None if no project is open.
        """
        if self._active_project is None:
            return None

        logbook = self._active_project.create_logbook(title, description)
        logger.debug("Created logbook: {}", title)
        return logbook

    # === Entry Management ===

    def set_active_entry(self, entry_id: UUID) -> bool:
        """Set the active entry in the current logbook.

        Args:
            entry_id: ID of the entry to activate.

        Returns:
            True if successful.
        """
        logbook = self.active_logbook
        if logbook is None:
            return False

        if logbook.set_active_entry(entry_id):
            entry = logbook.active_entry
            logger.debug(
                "Active entry changed: {}", entry.get_title() if entry else None
            )
            self.active_entry_changed.emit(entry)
            return True
        return False

    def create_note_entry(self) -> LogbookEntry | None:
        """Create a new note entry and activate it.

        Creates an empty note entry that the user can immediately edit.

        Returns:
            The created entry or None if no logbook is active.
        """
        logbook = self.active_logbook
        if logbook is None:
            return None

        entry = logbook.add_note("")  # Empty content - user will edit
        logger.info("Created new note entry")
        self.entry_added.emit(entry)
        self.active_entry_changed.emit(entry)
        return entry

    def update_entry_content(self, entry_id: UUID, content: str) -> bool:
        """Update the content of an entry.

        Args:
            entry_id: ID of the entry to update.
            content: New markdown content.

        Returns:
            True if successful.
        """
        logbook = self.active_logbook
        if logbook is None:
            return False

        entry = logbook.get_entry(entry_id)
        if entry is None or entry.protected:
            return False

        entry.content = content
        logbook.modified = datetime.now()
        return True

    # === Entry Creation Helpers ===

    def add_note(self, content: str) -> LogbookEntry | None:
        """Add a user note to the active logbook.

        Args:
            content: Markdown content for the note.

        Returns:
            The created entry or None if no logbook is active.
        """
        logbook = self.active_logbook
        if logbook is None:
            return None

        entry = logbook.add_note(content)
        logger.debug("Added note to logbook")
        self.entry_added.emit(entry)
        return entry

    def add_action_entry(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> LogbookEntry | None:
        """Add a device action entry (protected).

        Args:
            content: Description of the action.
            metadata: Additional action metadata.

        Returns:
            The created entry or None.
        """
        logbook = self.active_logbook
        if logbook is None:
            return None

        entry = logbook.add_system_entry(
            content=content,
            entry_type=EntryType.ACTION,
            metadata=metadata,
        )
        logger.debug("Added action entry to logbook")
        self.entry_added.emit(entry)
        return entry

    def add_scan_entry(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> LogbookEntry | None:
        """Add a scan entry (protected).

        Args:
            content: Scan description.
            metadata: Scan metadata (uid, plan, etc.).

        Returns:
            The created entry or None.
        """
        logbook = self.active_logbook
        if logbook is None:
            return None

        entry = logbook.add_system_entry(
            content=content,
            entry_type=EntryType.SCAN,
            metadata=metadata,
        )
        logger.debug("Added scan entry to logbook")
        self.entry_added.emit(entry)
        return entry

    def add_system_entry(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> LogbookEntry | None:
        """Add a system event entry (protected).

        Args:
            content: System event description.
            metadata: Additional metadata.

        Returns:
            The created entry or None.
        """
        logbook = self.active_logbook
        if logbook is None:
            return None

        entry = logbook.add_system_entry(
            content=content,
            entry_type=EntryType.SYSTEM,
            metadata=metadata,
        )
        logger.debug("Added system entry to logbook")
        self.entry_added.emit(entry)
        return entry

    # === Introspection ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with service state information.
        """
        project = self._active_project
        logbook = self.active_logbook

        return {
            "has_project": self.has_project,
            "project": {
                "id": str(project.id),
                "name": project.name,
                "description": project.metadata.description,
                "logbook_count": len(project.logbooks),
                "active_logbook_id": str(project.active_logbook_id)
                if project.active_logbook_id
                else None,
            }
            if project
            else None,
            "active_logbook": {
                "id": str(logbook.id),
                "title": logbook.title,
                "entry_count": len(logbook.entries),
            }
            if logbook
            else None,
            "recent_projects": self._recent_projects,
        }
