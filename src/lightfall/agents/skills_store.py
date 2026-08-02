"""Shipped-skills store: resolve and materialize SKILL.md-bearing skill dirs.

A "skill" is any directory containing a ``SKILL.md`` file (plus an optional
``references/`` subdirectory). Skills are discovered from three sources, in
increasing precedence:

1. Built-in skills shipped with Lightfall (``builtin_skills_dir()``).
2. Beamline-provided skills (caller-supplied ``extra_dirs``).
3. User skills under ``~/lightfall/skills`` (``user_skills_dir()``).

Later sources override earlier ones for skills sharing the same name. A
``_drafts`` subdirectory under any skill root is never resolved (phase-3
prep: drafts are staged there before promotion).

Body text may contain ``{{variable}}`` placeholders (see
``lightfall.agents.spec.resolve_template``); ``materialize_skills`` resolves
them against ``template_variables()`` by default.
"""

from __future__ import annotations

import getpass
import shutil
from pathlib import Path

from lightfall.agents.spec import AgentSpecError, resolve_template
from lightfall.utils.logging import logger

_DRAFTS_DIR_NAME = "_drafts"


def builtin_skills_dir() -> Path:
    """Return the package directory containing built-in shipped skills."""
    return Path(__file__).parent.parent / "skills" / "builtin"


def user_skills_dir() -> Path:
    """Return (and create) the user's skills directory: ``~/lightfall/skills``."""
    path = Path.home() / "lightfall" / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iter_skill_dirs(root: Path):
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name == _DRAFTS_DIR_NAME:
            continue
        if (child / "SKILL.md").is_file():
            yield child


def resolve_skills(extra_dirs: list[Path] | None = None) -> dict[str, Path]:
    """Resolve skill name -> skill directory across all roots.

    Precedence (later wins): builtin < extra_dirs (beamline) < user.
    """
    resolved: dict[str, Path] = {}
    for root in (builtin_skills_dir(), *(extra_dirs or []), user_skills_dir()):
        for skill_dir in _iter_skill_dirs(root):
            resolved[skill_dir.name] = skill_dir
    return resolved


def template_variables() -> dict[str, str]:
    """Return the live values available for ``{{variable}}`` substitution.

    - ``beamline``: the ``tiled_beamline`` preference, read the same way
      ``current_esaf`` reads it, falling back to the literal
      ``"<not configured>"`` when unset or unreadable.
    - ``user``: the current OS username (empty string on failure).
    - ``endstation``: reserved for future use; empty string for now.
    """
    return {
        "beamline": _read_beamline(),
        "user": _read_username(),
        "endstation": "",
    }


def _read_beamline() -> str:
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        beamline = prefs.get("tiled_beamline", None) or None
    except Exception:  # noqa: BLE001
        beamline = None
    return beamline or "<not configured>"


def _read_username() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return ""


def materialize_skills(
    names: tuple[str, ...],
    session_plugin_dir: Path,
    variables: dict[str, str] | None = None,
) -> list[str]:
    """Copy each resolved skill in `names` into `<session_plugin_dir>/skills/<name>/`.

    Unknown names are skipped with a warning. SKILL.md's body (everything
    after the frontmatter's closing ``---``) has ``{{variable}}`` placeholders
    resolved against `variables` (defaulting to `template_variables()`); an
    unknown placeholder logs a warning and the skill is materialized with its
    raw (unresolved) body rather than being dropped.

    Returns the list of names actually materialized (in `names` order).
    """
    resolved = resolve_skills()
    effective_variables = variables if variables is not None else template_variables()

    materialized: list[str] = []
    dest_root = session_plugin_dir / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)

    for name in names:
        src = resolved.get(name)
        if src is None:
            logger.warning("skills_store: unknown skill '{}'; skipping", name)
            continue

        dest = dest_root / name
        shutil.copytree(src, dest, dirs_exist_ok=True)
        _resolve_skill_md_template(dest / "SKILL.md", effective_variables)
        materialized.append(name)

    return materialized


def _resolve_skill_md_template(skill_md: Path, variables: dict[str, str]) -> None:
    if not skill_md.is_file():
        return
    text = skill_md.read_text(encoding="utf-8")
    # text is "---\n<meta>\n---\n\n<body>"; split on the SECOND "---\n" to
    # isolate the body from the frontmatter block.
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        # No (or malformed) frontmatter; nothing safe to split -- leave as-is.
        return
    head = "---\n" + parts[1] + "---\n"
    body = parts[2]

    try:
        resolved_body = resolve_template(body, variables)
    except AgentSpecError as e:
        logger.warning(
            "skills_store: {} contains an unresolvable template variable ({}); "
            "materializing with raw body",
            skill_md, e,
        )
        return

    skill_md.write_text(head + resolved_body, encoding="utf-8")
