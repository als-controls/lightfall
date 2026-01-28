"""Welcome project factory for first-launch experience.

This module creates a default "Welcome" project with introductory
content when NCS is launched for the first time or when no recent
project exists.
"""

from __future__ import annotations

from lucid.project.model import (
    EntrySource,
    EntryType,
    Logbook,
    LogbookEntry,
    Project,
    ProjectMetadata,
)

# Welcome content as markdown - this will be a protected region
WELCOME_CONTENT = """\
Welcome to NCS (New Control System)!

This is your experiment logbook. It automatically records your actions
and lets you add notes as you work.

**Getting Started:**

- **Add notes** using the toolbar or by typing directly below
- **System entries** (like motor moves and scans) are recorded automatically
- **Protected entries** have a gray background and cannot be edited

**Logbook Entry Types:**

| Icon | Type | Description |
|------|------|-------------|
| (none) | Note | Your own notes and observations |
| [Action] | Action | Device commands (move, set) |
| [Scan] | Scan | Data acquisition scans |
| [Snapshot] | Snapshot | Saved device states |
| [System] | System | Session events and errors |

**Tips:**

- Switch between **Visual** and **Markdown** modes using the toolbar
- Your notes are saved with the project
- Use the LLM assistant panel for help with controls and analysis

---

*This welcome message is protected and cannot be edited.
Create a new project from File > New Project to start fresh.*\
"""


def create_welcome_project() -> Project:
    """Create a welcome project for first-launch experience.

    Returns:
        A Project with a single logbook containing welcome content.
    """
    # Create project metadata
    metadata = ProjectMetadata(
        name="Welcome",
        description="Welcome project for new NCS users",
    )

    # Create the project
    project = Project(metadata=metadata)

    # Create welcome logbook
    logbook = Logbook(
        title="Welcome to NCS",
        description="Introduction to the NCS experiment logbook",
    )

    # Add welcome entry as protected system content
    welcome_entry = LogbookEntry(
        entry_type=EntryType.SYSTEM,
        source=EntrySource.SYSTEM,
        content=WELCOME_CONTENT,
        protected=True,
        metadata={"type": "welcome", "version": "1.0"},
    )
    logbook.add_entry(welcome_entry)

    # Add the logbook to project
    project.logbooks.append(logbook)
    project.active_logbook_id = logbook.id

    return project


def get_welcome_markdown() -> str:
    """Get the welcome content as rendered markdown.

    This is useful for displaying in a LogbookWidget directly
    without going through the full project/logbook model.

    Returns:
        Rendered markdown with protection markers.
    """
    project = create_welcome_project()
    logbook = project.active_logbook
    if logbook:
        return logbook.to_markdown()
    return ""
