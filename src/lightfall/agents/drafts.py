"""Skill drafts: persistence, provenance, and revision detection.

Drafts are staged skill files stored separately from shipped skills, with
provenance frontmatter to track author, creation date, and session context.
When a draft name collides with an active skill, it's written as a `.proposed`
revision rather than overwriting the draft's main file.

Descriptions are sanitized (YAML-quoted) to prevent frontmatter injection when
drafts are later promoted to active skills parsed by real YAML loaders.
"""

from __future__ import annotations

import json
import re
import shutil
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
    # Description is JSON-quoted for YAML safety to prevent frontmatter injection.
    frontmatter_name = name.replace("_", "-")
    description_quoted = json.dumps(description)
    frontmatter_lines = [
        "---",
        f"name: {frontmatter_name}",
        f"description: {description_quoted}",
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
        # Delete any stale SKILL.md draft that this revision supersedes.
        stale_draft = draft_dir / "SKILL.md"
        if stale_draft.exists():
            stale_draft.unlink()
            logger.info(
                "drafts: superseded stale draft '{}' with proposed revision",
                name,
            )
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


def approve_draft(name: str) -> Path:
    """Approve a draft, promoting it into the active skill set.

    Non-revision drafts (``_drafts/<name>/SKILL.md``) are moved wholesale to
    ``user_skills_dir()/<name>``. Revision drafts (``SKILL.md.proposed``)
    overwrite the active skill's ``SKILL.md`` in place when the active skill
    already resolves to user scope; otherwise a user-scope shadow directory
    is created so the shadow copy takes precedence over builtin/beamline.
    The draft directory is deleted afterward.

    Returns the path to the now-active ``SKILL.md``.

    Not atomic: if the draft directory removal fails after promotion/shadow
    content is written, the draft dir may remain as an orphan. This is a
    harmless leftover -- re-approving or re-drafting under the same name is
    safe.

    Raises:
        DraftError: If no draft with `name` exists.
    """
    draft_dir = drafts_dir() / name
    proposed_path = draft_dir / "SKILL.md.proposed"
    plain_path = draft_dir / "SKILL.md"

    if not draft_dir.is_dir() or not (proposed_path.exists() or plain_path.exists()):
        raise DraftError(f"No draft named '{name}' exists")

    author, created = _parse_frontmatter(proposed_path if proposed_path.exists() else plain_path)

    if proposed_path.exists():
        user_dir = user_skills_dir()
        active_path = resolve_skills().get(name)
        active_is_user_scope = active_path is not None and active_path == user_dir / name

        if active_is_user_scope:
            target_skill_md = active_path / "SKILL.md"
        else:
            shadow_dir = user_dir / name
            if active_path is not None:
                # Carry the shipped skill's full asset set (references/, etc.)
                # into the shadow so whole-dir resolution doesn't strand them.
                shutil.copytree(active_path, shadow_dir, dirs_exist_ok=True)
            else:
                shadow_dir.mkdir(parents=True, exist_ok=True)
            target_skill_md = shadow_dir / "SKILL.md"

        target_skill_md.write_text(
            proposed_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        shutil.rmtree(draft_dir)
        logger.info(
            "drafts: approved revision '{}' (author={}, created={}) -> {}",
            name, author, created, target_skill_md,
        )
        return target_skill_md

    target_dir = user_skills_dir() / name
    if target_dir.exists():
        raise DraftError(
            f"Cannot approve draft '{name}': a user skill directory already exists"
        )
    shutil.move(str(draft_dir), str(target_dir))
    result = target_dir / "SKILL.md"
    logger.info(
        "drafts: approved new draft '{}' (author={}, created={}) -> {}",
        name, author, created, result,
    )
    return result


def reject_draft(name: str) -> None:
    """Delete a draft entirely.

    Raises:
        DraftError: If no draft with `name` exists.
    """
    draft_dir = drafts_dir() / name
    if not draft_dir.is_dir():
        raise DraftError(f"No draft named '{name}' exists")
    shutil.rmtree(draft_dir)
    logger.info("drafts: rejected draft '{}'", name)


def _validate_name(name: str) -> None:
    """Validate skill name format."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise DraftError(
            f"Invalid skill name '{name}': must match ^[a-z0-9][a-z0-9_-]{{0,63}}$"
        )


def _validate_description(description: str) -> None:
    """Validate description.

    Rejects descriptions with embedded newlines/carriage returns (prevents
    frontmatter injection) and whitespace-only descriptions. Also rejects
    descriptions longer than 1024 chars.
    """
    # Check for newlines/carriage returns.
    if "\n" in description or "\r" in description:
        raise DraftError(
            "Invalid description: cannot contain newlines or carriage returns"
        )

    # Strip and check for emptiness or length.
    stripped = description.strip()
    if not stripped or len(stripped) > 1024:
        raise DraftError(
            "Invalid description: must be non-empty (after stripping) and ≤1024 chars"
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
