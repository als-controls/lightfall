"""Qt-free list models for the Agents & Skills editor panel.

These are plain dataclasses/functions -- NOT QAbstractItemModel subclasses --
so the panel's row-building logic can be unit tested without a QApplication
and without importing Qt at all. The panel widget is responsible for wrapping
these rows in whatever Qt view model it needs.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lightfall.agents import skills_store
from lightfall.agents.drafts import list_drafts
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.agents.registry import AgentSpecRegistry

# Mirrors AgentSpecRegistry's precedence order (core < beamline < user); kept
# local so this module never has to import registry.py (and its Qt deps) at
# module load time.
_SCOPE_ORDER: tuple[str, ...] = ("core", "beamline", "user")


class EditorError(ValueError):
    """Raised when an editor action (copy/reset) can't be performed."""

    pass


@dataclass(frozen=True)
class AgentRow:
    name: str
    scope: str
    description: str
    editable: bool
    shadowed_scopes: tuple[str, ...]
    error: str | None
    source_path: Path


@dataclass(frozen=True)
class SkillRow:
    name: str
    scope: str
    path: Path
    editable: bool


@dataclass(frozen=True)
class DraftRow:
    name: str
    path: Path
    is_revision: bool
    author: str | None
    created: str | None
    diff_target: Path | None


def agent_rows(registry: AgentSpecRegistry) -> list[AgentRow]:
    """Build one row per resolved agent spec, plus one per rejected file."""
    all_files = registry.all_scope_files()
    rows: list[AgentRow] = []

    for spec in sorted(registry.specs(), key=lambda s: s.name):
        entries = all_files.get(spec.name, [])
        shadowed = tuple(
            scope
            for scope in _SCOPE_ORDER
            if scope != spec.scope and any(s == scope for s, _ in entries)
        )
        rows.append(
            AgentRow(
                name=spec.name,
                scope=spec.scope,
                description=spec.description,
                editable=spec.scope == "user",
                shadowed_scopes=shadowed,
                error=None,
                source_path=spec.source_path,
            )
        )

    for path, error in registry.errors():
        name = path.stem
        scope = _scope_for_error_path(all_files, path)
        rows.append(
            AgentRow(
                name=name,
                scope=scope,
                description="",
                editable=False,
                shadowed_scopes=(),
                error=error,
                source_path=path,
            )
        )

    return rows


def _scope_for_error_path(
    all_files: dict[str, list[tuple[str, Path]]], path: Path
) -> str:
    for entries in all_files.values():
        for scope, entry_path in entries:
            if entry_path == path:
                return scope
    return "unknown"


def skill_rows() -> list[SkillRow]:
    """Build one row per resolved skill, attributing it to a scope by root."""
    resolved = skills_store.resolve_skills()
    builtin_root = skills_store.builtin_skills_dir()
    user_root = skills_store.user_skills_dir()

    rows: list[SkillRow] = []
    for name in sorted(resolved):
        path = resolved[name]
        if path.parent == builtin_root:
            scope = "core"
        elif path.parent == user_root:
            scope = "user"
        else:
            scope = "beamline"
        rows.append(SkillRow(name=name, scope=scope, path=path, editable=scope == "user"))
    return rows


def draft_rows() -> list[DraftRow]:
    """Thin wrapper over list_drafts() adding the active SKILL.md diff target."""
    resolved = skills_store.resolve_skills()
    rows: list[DraftRow] = []
    for draft in list_drafts():
        diff_target: Path | None = None
        if draft["is_revision"]:
            active_dir = resolved.get(draft["name"])
            if active_dir is not None:
                candidate = active_dir / "SKILL.md"
                if candidate.is_file():
                    diff_target = candidate
        rows.append(
            DraftRow(
                name=draft["name"],
                path=draft["path"],
                is_revision=draft["is_revision"],
                author=draft["author"],
                created=draft["created"],
                diff_target=diff_target,
            )
        )
    return rows


def copy_to_user(name: str, registry: AgentSpecRegistry) -> Path:
    """Copy the resolved agent spec `name` into the user scope as an editable shadow."""
    from lightfall.agents.registry import user_agents_dir

    spec = registry.get(name)
    if spec is None:
        raise EditorError(f"No agent named '{name}' is resolved; cannot copy to user scope")

    dest = user_agents_dir() / f"{name}.md"
    dest.write_text(spec.source_path.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info("agent_editor: copied agent '{}' to user scope -> {}", name, dest)
    registry.reload()
    return dest


def copy_skill_to_user(name: str) -> Path:
    """Copy the resolved skill `name` into the user scope as an editable shadow."""
    resolved = skills_store.resolve_skills()
    src = resolved.get(name)
    if src is None:
        raise EditorError(f"No skill named '{name}' is resolved; cannot copy to user scope")

    dest = skills_store.user_skills_dir() / name
    shutil.copytree(src, dest, dirs_exist_ok=True)
    logger.info("agent_editor: copied skill '{}' to user scope -> {}", name, dest)
    return dest


def reset_to_default(name: str) -> None:
    """Delete the user-scope shadow for agent `name`, reverting to core/beamline.

    Raises:
        EditorError: If there is no user-scope shadow file for `name`.
    """
    from lightfall.agents.registry import user_agents_dir

    shadow = user_agents_dir() / f"{name}.md"
    if not shadow.is_file():
        raise EditorError(f"'{name}' has no user-scope shadow to reset")
    shadow.unlink()
    logger.info("agent_editor: reset agent '{}' to default (removed user shadow)", name)
