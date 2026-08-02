"""Skill drafts: persistence, provenance, and revision detection.

Drafts are staged skill files stored separately from shipped skills, with
provenance frontmatter to track author, creation date, and session context.
When a draft name collides with an active skill, it's written as a `.proposed`
revision rather than overwriting the draft's main file.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from lightfall.agents.skills_store import resolve_skills, user_skills_dir
from lightfall.utils.logging import logger


class DraftError(ValueError):
    """Raised when draft name, description, or body fails validation."""

    pass


def drafts_dir() -> Path:
    """Return (and create) the drafts directory under user skills."""
    path = user_skills_dir() / "_drafts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_draft(
    name: str,
    description: str,
    body: str,
    *,
    author: str,
    session_id: str | None = None,
) -> tuple[Path, bool]:
    """Save a skill draft with provenance and collision detection.

    Validates name (must match ^[a-z0-9][a-z0-9_-]{0,63}$), description
    (non-empty, ≤1024 chars), and body (non-empty). Writes frontmatter with
    provenance keys (author, created ISO date, optional session_id).

    If name is NOT an active skill: writes to _drafts/<name>/SKILL.md and
    returns (path, False). If name IS an active skill: writes to
    _drafts/<name>/SKILL.md.proposed and returns (path, True).

    Redrafts of existing drafts overwrite the previous version.

    Args:
        name: Skill name (lowercase alphanumeric, hyphens, underscores; 1-64 chars).
        description: Human-readable description (non-empty, ≤1024 chars).
        body: Skill body content (non-empty).
        author: Provenance author name.
        session_id: Optional session identifier for provenance.

    Returns:
        (Path to written file, is_revision flag). is_revision is True if name
        collides with an active skill.

    Raises:
        DraftError: If name, description, or body fails validation.
    """
    _validate_name(name)
    _validate_description(description)
    _validate_body(body)

    is_revision = name in resolve_skills()
    created = datetime.now().date().isoformat()

    # Build frontmatter with name normalized to hyphens for the field.
    frontmatter_name = name.replace("_", "-")
    frontmatter_lines = [
        "---",
        f"name: {frontmatter_name}",
        f"description: {description}",
        f"lightfall-draft:",
        f"  author: {author}",
        f"  created: {created}",
    ]
    if session_id is not None:
        frontmatter_lines.append(f"  session_id: {session_id}")
    frontmatter_lines.extend(["---", ""])

    frontmatter = "\n".join(frontmatter_lines)
    content = frontmatter + body

    # Determine output path and check for existing draft.
    draft_dir = drafts_dir() / name
    draft_dir.mkdir(parents=True, exist_ok=True)

    if is_revision:
        path = draft_dir / "SKILL.md.proposed"
    else:
        path = draft_dir / "SKILL.md"
        # Check if we're overwriting an existing draft.
        if path.exists():
            logger.info("drafts: overwriting existing draft '{}'", name)

    path.write_text(content, encoding="utf-8")
    return path, is_revision


def list_drafts() -> list[dict]:
    """List all drafts with parsed provenance.

    Returns a list of dicts with keys:
    - name: Draft name.
    - path: Path to the draft file (SKILL.md or SKILL.md.proposed).
    - is_revision: True if a .proposed file, False otherwise.
    - author: Parsed author from frontmatter, or None if unparsable.
    - created: Parsed created date (ISO string), or None if unparsable.

    Drafts are sorted by name. Unparsable frontmatter logs a warning and
    yields None for author/created.
    """
    drafts_root = drafts_dir()
    if not drafts_root.exists():
        return []

    results = []
    for draft_dir in sorted(drafts_root.iterdir()):
        if not draft_dir.is_dir():
            continue

        name = draft_dir.name

        # Check for both SKILL.md and SKILL.md.proposed; prefer the revision if both exist.
        skill_file = draft_dir / "SKILL.md"
        proposed_file = draft_dir / "SKILL.md.proposed"

        if proposed_file.exists():
            path = proposed_file
            is_revision = True
        elif skill_file.exists():
            path = skill_file
            is_revision = False
        else:
            # No draft file in this directory; skip.
            continue

        # Parse frontmatter.
        author, created = _parse_frontmatter(path)

        results.append(
            {
                "name": name,
                "path": path,
                "is_revision": is_revision,
                "author": author,
                "created": created,
            }
        )

    # Sort by name.
    results.sort(key=lambda d: d["name"])
    return results


def _validate_name(name: str) -> None:
    """Validate skill name format."""
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", name):
        raise DraftError(
            f"Invalid skill name '{name}': must match ^[a-z0-9][a-z0-9_-]{{0,63}}$"
        )


def _validate_description(description: str) -> None:
    """Validate description."""
    if not description or len(description) > 1024:
        raise DraftError(
            f"Invalid description: must be non-empty and ≤1024 chars"
        )


def _validate_body(body: str) -> None:
    """Validate body."""
    if not body:
        raise DraftError("Invalid body: must be non-empty")


def _parse_frontmatter(path: Path) -> tuple[str | None, str | None]:
    """Parse author and created from draft frontmatter.

    Returns (author, created) tuple. If parsing fails, both are None and a
    warning is logged.
    """
    try:
        text = path.read_text(encoding="utf-8")
        # Find frontmatter block between "---\n" markers.
        parts = text.split("---\n", 2)
        if len(parts) < 2:
            _log_parse_error(path, "No frontmatter block found")
            return None, None

        frontmatter_text = parts[1]
        author = None
        created = None

        for line in frontmatter_text.split("\n"):
            line = line.strip()
            if line.startswith("author:"):
                author = line.split(":", 1)[1].strip()
            elif line.startswith("created:"):
                created = line.split(":", 1)[1].strip()

        # Both must be present for successful parse.
        if author is None or created is None:
            _log_parse_error(path, "Missing author or created in frontmatter")
            return None, None

        return author, created
    except Exception as e:
        _log_parse_error(path, str(e))
        return None, None


def _log_parse_error(path: Path, reason: str) -> None:
    """Log a frontmatter parse error."""
    logger.warning(
        "drafts: unable to parse frontmatter in '{}': {}",
        path,
        reason,
    )
