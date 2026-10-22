"""Per-agent "always allow" approvals for skills an agent loads on request.

When an agent asks for a skill it wasn't configured with (``get_skill`` on
the ``skills`` MCP server), the user can allow it once or always allow it
for that agent. "Always" is recorded here, keyed by agent name, and
``skills_store.materialize_skills(approved_for=<agent>)`` copies those
skills into the agent's later sessions.

Stored as JSON at ``~/lightfall/skill_approvals.json``::

    {"version": 1, "agents": {"lightfall": ["gpcam:kernel-designer"]}}

Ids are ``skills_store.skill_catalog`` ids. Removing an entry (by editing
the file or ``revoke_skill``) takes effect from the agent's next session.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from lightfall.utils.logging import logger

_VERSION = 1
_lock = threading.Lock()


def approvals_path() -> Path:
    """Return the approvals file path: ``~/lightfall/skill_approvals.json``."""
    return Path.home() / "lightfall" / "skill_approvals.json"


def _load() -> dict[str, list[str]]:
    path = approvals_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        agents = data.get("agents", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("skill_approvals: ignoring unreadable {}: {}", path, exc)
        return {}
    return {
        str(agent): [str(i) for i in ids if isinstance(i, str)]
        for agent, ids in agents.items()
        if isinstance(ids, list)
    }


def _save(agents: dict[str, list[str]]) -> None:
    path = approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": _VERSION, "agents": agents}, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".skill_approvals.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def approved_skill_ids(agent: str) -> tuple[str, ...]:
    """Skill ids the user chose "always allow" for, for `agent`."""
    with _lock:
        return tuple(_load().get(agent, ()))


def approve_skill(agent: str, skill_id: str) -> None:
    """Record "always allow" of `skill_id` for `agent`."""
    with _lock:
        agents = _load()
        ids = agents.setdefault(agent, [])
        if skill_id not in ids:
            ids.append(skill_id)
            ids.sort()
            _save(agents)
            logger.info("skill_approvals: agent '{}' may always load '{}'", agent, skill_id)


def revoke_skill(agent: str, skill_id: str) -> bool:
    """Remove an "always allow" approval. Returns whether one existed."""
    with _lock:
        agents = _load()
        ids = agents.get(agent, [])
        if skill_id not in ids:
            return False
        ids.remove(skill_id)
        if not ids:
            del agents[agent]
        _save(agents)
        return True
