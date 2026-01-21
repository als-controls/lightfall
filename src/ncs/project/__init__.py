"""Project management for NCS.

This package provides:
- Project: Container for experiment work
- Logbook: Ordered collection of entries
- LogbookEntry: Individual log entries
- ProjectService: Singleton for active project management
- WelcomeProject: First-launch experience
"""

from ncs.project.model import (
    Attachment,
    EntrySource,
    EntryType,
    Logbook,
    LogbookEntry,
    Project,
    ProjectMetadata,
)
from ncs.project.service import ProjectService
from ncs.project.welcome import create_welcome_project, get_welcome_markdown

__all__ = [
    # Models
    "Attachment",
    "EntrySource",
    "EntryType",
    "Logbook",
    "LogbookEntry",
    "Project",
    "ProjectMetadata",
    # Service
    "ProjectService",
    # Welcome
    "create_welcome_project",
    "get_welcome_markdown",
]
