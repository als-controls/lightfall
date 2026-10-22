"""Shipped-skills store: resolve and materialize SKILL.md-bearing skill dirs.

A "skill" is any directory containing a ``SKILL.md`` file (plus an optional
``references/`` subdirectory). Skills are discovered from these sources, in
increasing precedence:

1. Built-in skills shipped with Lightfall (``builtin_skills_dir()``).
2. Skills advertised by installed Python packages through the
   ``agent_skills`` entry-point group (``entry_point_skill_dirs()``), e.g.
   gpCAM's design skills.
3. Beamline-provided skills (caller-supplied ``extra_dirs``).
4. Skills packaged inside ToolPlugins (``ToolPlugin.get_skill_dir()``,
   collected via ``plugin_skill_dirs()``).
5. User skills under ``~/lightfall/skills`` (``user_skills_dir()``).

Later sources override earlier ones for skills sharing the same name. A
``_drafts`` subdirectory under any skill root is never resolved (phase-3
prep: drafts are staged there before promotion).

Body text may contain ``{{variable}}`` placeholders (see
``lightfall.agents.spec.resolve_template``); ``materialize_skills`` resolves
them against ``template_variables()`` by default.
"""

from __future__ import annotations

import getpass
import importlib.metadata
import importlib.resources
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from lightfall.agents.spec import _FRONTMATTER_RE, AgentSpecError, resolve_template
from lightfall.utils.logging import logger

_DRAFTS_DIR_NAME = "_drafts"

#: Entry-point group through which installed packages advertise agent skills.
#: Contract: each entry point loads to a package; every subfolder of
#: ``importlib.resources.files(<package>)`` that contains a ``SKILL.md`` is one
#: skill, named after its folder. gpCAM registers ``gpcam = "gpcam.skills"``.
ENTRY_POINT_GROUP = "agent_skills"


def builtin_skills_dir() -> Path:
    """Return the package directory containing built-in shipped skills."""
    return Path(__file__).parent.parent / "skills" / "builtin"


def user_skills_dir() -> Path:
    """Return (and create) the user's skills directory: ``~/lightfall/skills``."""
    path = Path.home() / "lightfall" / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugin_skill_dirs() -> dict[str, Path]:
    """Skill dirs contributed by registered ToolPlugins via ``get_skill_dir()``.

    Keyed by plugin name. Dirs missing SKILL.md are skipped with a warning.
    Returns {} if the ToolRegistry is unavailable (e.g. headless tooling).
    """
    try:
        from lightfall.ui.panels.claude.tool_registry import ToolRegistry

        plugins = ToolRegistry.get_instance().get_plugins()
    except Exception as exc:  # noqa: BLE001 - skills must resolve without the registry
        logger.debug("plugin_skill_dirs: ToolRegistry unavailable: {}", exc)
        return {}

    dirs: dict[str, Path] = {}
    for plugin in plugins:
        try:
            skill_dir = plugin.get_skill_dir()
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
            logger.warning("plugin '{}': get_skill_dir() failed: {}", plugin.name, exc)
            continue
        if skill_dir is None:
            continue
        if not (Path(skill_dir) / "SKILL.md").is_file():
            logger.warning(
                "plugin '{}': skill dir {} has no SKILL.md; skipping", plugin.name, skill_dir
            )
            continue
        dirs[plugin.name] = Path(skill_dir)
    return dirs


def plugin_extra_skill_dirs() -> dict[str, Path]:
    """Independently-loadable skills contributed by *enabled* ToolPlugins via
    ``get_extra_skill_dirs()``, keyed by skill name.

    These are skills a plugin sources from its own dependencies (e.g. gpCAM's
    design skills) rather than from a Lightfall skills root. Dirs missing
    SKILL.md are skipped with a warning. Returns {} if the ToolRegistry is
    unavailable (e.g. headless tooling).
    """
    try:
        from lightfall.ui.panels.claude.tool_registry import ToolRegistry

        plugins = ToolRegistry.get_instance().enabled_plugins()
    except Exception as exc:  # noqa: BLE001 - skills must resolve without the registry
        logger.debug("plugin_extra_skill_dirs: ToolRegistry unavailable: {}", exc)
        return {}

    dirs: dict[str, Path] = {}
    for plugin in plugins:
        try:
            extra = plugin.get_extra_skill_dirs()
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
            logger.warning("plugin '{}': get_extra_skill_dirs() failed: {}", plugin.name, exc)
            continue
        for skill_name, skill_dir in (extra or {}).items():
            if not (Path(skill_dir) / "SKILL.md").is_file():
                logger.warning(
                    "plugin '{}': extra skill '{}' dir {} has no SKILL.md; skipping",
                    plugin.name, skill_name, skill_dir,
                )
                continue
            dirs[skill_name] = Path(skill_dir)
    return dirs


@dataclass(frozen=True)
class EntryPointSkill:
    """One skill folder offered by an ``agent_skills`` entry point."""

    entry_point: str  #: entry-point name, e.g. ``gpcam``; the skill's namespace
    distribution: str | None  #: installing distribution, e.g. ``gpcam``
    path: Path


def entry_point_skills() -> list[EntryPointSkill]:
    """Every skill offered through the ``agent_skills`` entry-point group.

    Each entry point must load to a package whose resource folder holds one
    subfolder per skill. An entry point that fails to load or resolve logs a
    warning and is skipped; folders without SKILL.md and ``_drafts`` are
    ignored. Entry points are read in name order. Skills from different
    entry points may share a name; see ``entry_point_skill_dirs``.
    """
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001 - skills must resolve without metadata
        logger.warning("entry_point_skills: reading entry points failed: {}", exc)
        return []

    skills: list[EntryPointSkill] = []
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            root = Path(str(importlib.resources.files(ep.load())))
        except Exception as exc:  # noqa: BLE001 - one bad entry point must not break the rest
            logger.warning(
                "agent_skills entry point '{}' ({}) failed to load: {}", ep.name, ep.value, exc
            )
            continue
        dist = getattr(ep, "dist", None)
        dist_name = getattr(dist, "name", None) if dist is not None else None
        for skill_dir in _iter_skill_dirs(root):
            skills.append(EntryPointSkill(ep.name, dist_name, skill_dir))
    return skills


def _entry_point_winners() -> dict[str, EntryPointSkill]:
    winners: dict[str, EntryPointSkill] = {}
    for skill in entry_point_skills():
        name = skill.path.name
        previous = winners.get(name)
        if previous is not None and previous.path.resolve() != skill.path.resolve():
            logger.warning(
                "agent_skills entry point '{}': skill '{}' at {} overrides {}",
                skill.entry_point, name, skill.path, previous.path,
            )
        winners[name] = skill
    return winners


def entry_point_skill_dirs() -> dict[str, Path]:
    """Skills advertised by installed packages via the ``agent_skills``
    entry-point group, keyed by skill name (the folder name).

    When two entry points offer the same skill name the later one (in
    entry-point name order) wins, with a warning. ``skill_catalog`` still
    lists both under namespaced ids.
    """
    return {name: skill.path for name, skill in _entry_point_winners().items()}


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

    Precedence (later wins): builtin < entry points < extra_dirs (beamline)
    < plugin < user.

    "entry points" are skills advertised by installed packages through the
    ``agent_skills`` group (see ``entry_point_skill_dirs``). "plugin" skills are single dirs contributed by registered ToolPlugins
    via ``get_skill_dir()``, keyed by plugin name (see ``plugin_skill_dirs``).
    """
    return {name: located.path for name, located in _resolve_located(extra_dirs).items()}


@dataclass(frozen=True)
class _Located:
    path: Path
    source: str  # "builtin" | "package" | "beamline" | "plugin" | "user"


def _resolve_located(extra_dirs: list[Path] | None) -> dict[str, _Located]:
    resolved: dict[str, _Located] = {}
    for skill_dir in _iter_skill_dirs(builtin_skills_dir()):
        resolved[skill_dir.name] = _Located(skill_dir, "builtin")
    for name, ep_skill in _entry_point_winners().items():
        resolved[name] = _Located(ep_skill.path, "package")
    for root in extra_dirs or []:
        for skill_dir in _iter_skill_dirs(root):
            resolved[skill_dir.name] = _Located(skill_dir, "beamline")
    for name, skill_dir in plugin_skill_dirs().items():
        resolved[name] = _Located(skill_dir, "plugin")
    for skill_dir in _iter_skill_dirs(user_skills_dir()):
        resolved[skill_dir.name] = _Located(skill_dir, "user")
    return resolved


@dataclass(frozen=True)
class SkillInfo:
    """One entry of the skill catalog an agent can browse."""

    id: str  #: ``<entry point>:<name>`` for package skills, else the bare name
    name: str  #: folder name; the name the skill is materialized under
    description: str  #: the SKILL.md frontmatter ``description`` ("" if none)
    source: str  #: "builtin" | "package" | "beamline" | "plugin" | "user"
    distribution: str | None  #: installing distribution, for package skills
    path: Path


def read_skill_description(skill_dir: Path) -> str:
    """Return the ``description`` from a skill's SKILL.md frontmatter, or ""."""
    try:
        m = _FRONTMATTER_RE.match((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        meta = yaml.safe_load(m.group(1)) if m else None
    except Exception:  # noqa: BLE001 - an unreadable skill just has no description
        return ""
    description = meta.get("description") if isinstance(meta, dict) else None
    return " ".join(description.split()) if isinstance(description, str) else ""


def skill_catalog(extra_dirs: list[Path] | None = None) -> dict[str, SkillInfo]:
    """Every skill an agent could load, keyed by id.

    Package skills (from ``agent_skills`` entry points) always get namespaced
    ids such as ``gpcam:kernel-designer``, including any whose bare name is
    shadowed by a higher-precedence source, so two packages may ship
    same-named skills. Skills from the other sources keep their bare names,
    and only the resolved winner of each name is listed.
    """
    catalog: dict[str, SkillInfo] = {}

    def add(skill_id: str, path: Path, source: str, distribution: str | None) -> None:
        catalog[skill_id] = SkillInfo(
            id=skill_id,
            name=path.name,
            description=read_skill_description(path),
            source=source,
            distribution=distribution,
            path=path,
        )

    for name, located in _resolve_located(extra_dirs).items():
        if located.source != "package":
            add(name, located.path, located.source, None)
    for ep_skill in entry_point_skills():
        add(f"{ep_skill.entry_point}:{ep_skill.path.name}", ep_skill.path, "package",
            ep_skill.distribution)
    return dict(sorted(catalog.items()))


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
    approved_for: str | None = None,
) -> list[str]:
    """Copy each resolved skill in `names` into `<session_plugin_dir>/skills/<name>/`.

    Unknown names are skipped with a warning. SKILL.md's body (everything
    after the frontmatter's closing ``---``) has ``{{variable}}`` placeholders
    resolved against `variables` (defaulting to `template_variables()`); an
    unknown placeholder logs a warning and the skill is materialized with its
    raw (unresolved) body rather than being dropped.

    When `approved_for` names an agent, the skills that agent was given
    "always allow" for (see ``lightfall.agents.skill_approvals``) are
    materialized too, under their folder names, unless that name is taken.

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

    if approved_for:
        from lightfall.agents.skill_approvals import approved_skill_ids

        approved = approved_skill_ids(approved_for)
        catalog = skill_catalog() if approved else {}
        for skill_id in approved:
            info = catalog.get(skill_id)
            if info is None:
                logger.warning(
                    "skills_store: agent '{}' has approved skill '{}', which no longer "
                    "resolves; skipping", approved_for, skill_id,
                )
                continue
            if info.name in materialized:
                continue
            dest = dest_root / info.name
            shutil.copytree(info.path, dest, dirs_exist_ok=True)
            _resolve_skill_md_template(dest / "SKILL.md", effective_variables)
            materialized.append(info.name)

    # Extra skills contributed by enabled plugins from their own dependencies
    # (e.g. gpCAM's design skills). Materialized under their own names so the
    # Skill tool can lazy-load them. Explicit `names` take precedence.
    for skill_name, src in plugin_extra_skill_dirs().items():
        if skill_name in materialized:
            continue
        dest = dest_root / skill_name
        shutil.copytree(src, dest, dirs_exist_ok=True)
        _resolve_skill_md_template(dest / "SKILL.md", effective_variables)
        materialized.append(skill_name)

    return materialized


def render_skill_md(text: str, variables: dict[str, str], origin: object = "SKILL.md") -> str:
    """Resolve ``{{variable}}`` placeholders in a SKILL.md body.

    The frontmatter is left untouched. Text without frontmatter, or whose
    body has an unknown placeholder (logged as a warning), is returned as is.
    """
    # text is "---\n<meta>\n---\n\n<body>"; split on the SECOND "---\n" to
    # isolate the body from the frontmatter block.
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        # No (or malformed) frontmatter; nothing safe to split -- leave as-is.
        return text
    head = "---\n" + parts[1] + "---\n"
    body = parts[2]

    try:
        resolved_body = resolve_template(body, variables)
    except AgentSpecError as e:
        logger.warning(
            "skills_store: {} contains an unresolvable template variable ({}); "
            "using the raw body",
            origin, e,
        )
        return text
    return head + resolved_body


def _resolve_skill_md_template(skill_md: Path, variables: dict[str, str]) -> None:
    if not skill_md.is_file():
        return
    text = skill_md.read_text(encoding="utf-8")
    rendered = render_skill_md(text, variables, origin=skill_md)
    if rendered != text:
        skill_md.write_text(rendered, encoding="utf-8")
