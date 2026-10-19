"""Generate the private agy homes (settings, plugin, rules, agent, skills).

agy reads ``<home>/.gemini/...`` (HOME/USERPROFILE overridden for the child), so a
private home keeps Lightfall's configuration away from the user's real
``~/.gemini``.  Layout and enablement record were verified live against agy 1.3.5.

Live findings that shape this module:

* agy does NOT expand ``${VAR}`` (nor ``$VAR`` / ``${env:VAR}`` / ``%VAR%``) in a
  plugin ``mcp_config.json``.  Per-session port and token are therefore written
  literally into a per-SESSION home: ``<spec>/sessions/<id>`` holds a real
  ``.gemini/config`` and a junction/symlink ``.gemini/antigravity-cli`` pointing at
  the per-SPEC store (settings.json, conversations, ...).
* The ``permissions.allow`` server part is matched literally: ``mcp(lightfall_*/*)``
  does not allow anything, ``mcp(lightfall_<key>/*)`` does.  One entry per server key.
* ``agy plugin enable lightfall`` writes ``<home>/.gemini/config/config.json``
  as ``{"plugins": {"lightfall": {"enabled": true}}}``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lightfall.agents.backend import SubagentSpec
from lightfall.agents.backends.agy.hook_policy import AGY_BUILTIN_TOOLS, SUBAGENT_TOOL
from lightfall.utils.logging import logger

PLUGIN_NAME = "lightfall"
DEFAULT_PROJECT = "lbl-gemini-enterprise-access"
HOOK_TIMEOUT_S = 290
OWNER_FILE = "owner.pid"
STALE_UNOWNED_S = 3600  # a session home with no readable owner.pid is swept after this

_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CREATE_NO_WINDOW = 0x08000000

# agy permission categories for the operator agent's built-ins.  Headless mode
# soft-denies an unlisted category before the hook can approve it.  Browser
# actuation is not offered, so it stays denied.
BUILTIN_ALLOW = ("read_file(*)", "write_file(*)", "command(*)", "read_url(*)")
BUILTIN_DENY = ("execute_url(*)",)

_OPERATOR_FRONTMATTER = (
    "---\n"
    "name: operator\n"
    "description: Lightfall beamline control assistant\n"
    "mainAgent: true\n"
    "subagent: false\n"
    "excludeDefaultComponents: true\n"
    "inheritCustomizations: true\n"
    "inheritMcp: true\n"
    # The same built-ins a Claude session gets (read, edit, shell, web); every
    # call is gated by the PreToolUse hook through PermissionManager.
    f"tools: [{', '.join(AGY_BUILTIN_TOOLS)}]\n"
    "---\n"
)

# Model aliases agy accepts in an agent file besides the ids `agy models` lists.
AGY_MODEL_ALIASES = frozenset({"inherit", "flash", "flash_lite", "pro"})

# A subagent gets the operator's built-ins minus the delegation tool: no nested
# delegation. Deliberately stricter than Claude Code, whose subagents may nest
# (CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH, default 3 since CLI 2.1.219).
_SUBAGENT_TOOLS = tuple(t for t in AGY_BUILTIN_TOOLS if t != SUBAGENT_TOOL)

_SUBAGENT_FRONTMATTER = (
    "---\n"
    "name: {name}\n"
    "description: {description}\n"
    "mainAgent: false\n"
    "subagent: true\n"
    "excludeDefaultComponents: true\n"
    # Both inherit flags route the subagent's tool calls to Lightfall's MCP
    # servers and through the PreToolUse hook (verified live, agy 1.4.2).
    "inheritCustomizations: true\n"
    "inheritMcp: true\n"
    f"tools: [{', '.join(_SUBAGENT_TOOLS)}]\n"
    "model: {model}\n"
    "---\n"
)


def lightfall_agy_home() -> Path:
    """Root of all Lightfall agy homes (created)."""
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"])
    else:
        base = Path.home() / ".config"
    p = base / "ncs" / "agy-home"
    p.mkdir(parents=True, exist_ok=True)
    return p


def spec_home(spec_name: str | None) -> Path:
    """Per-AgentSpec home (``lightfall`` for the main assistant); pure path."""
    name = spec_name or "lightfall"
    if not _KEY_RE.match(name) or name in (".", ".."):
        raise ValueError(f"invalid agent spec name {spec_name!r} (allowed: A-Za-z0-9_.-)")
    return lightfall_agy_home() / name


def session_home_path(spec_name: str | None, session_id: str) -> Path:
    """Per-session home; pure path."""
    return spec_home(spec_name) / "sessions" / session_id


def _is_junction(p: Path) -> bool:
    isj = getattr(os.path, "isjunction", None)
    return bool(isj and isj(p))


def _link_points_at(link: Path, target: Path) -> bool:
    try:
        return os.path.samefile(link, target)
    except OSError:
        return False


def _make_dir_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError):
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_link(link: Path) -> None:
    """Remove a junction/symlink itself (never its target); no-op if absent."""
    if _is_junction(link):
        os.rmdir(link)
    elif link.is_symlink():
        os.unlink(link)


def prepare_session_home(spec_name: str | None, session_id: str) -> Path:
    """Create the session home: real ``.gemini/config``, linked ``antigravity-cli``."""
    store = spec_home(spec_name) / ".gemini" / "antigravity-cli"
    store.mkdir(parents=True, exist_ok=True)
    session = session_home_path(spec_name, session_id)
    (session / ".gemini" / "config").mkdir(parents=True, exist_ok=True)
    link = session / ".gemini" / "antigravity-cli"
    if os.path.lexists(link):
        if _link_points_at(link, store):
            return session
        _remove_link(link)
    _make_dir_link(link, store)
    return session


def remove_session_home(path: Path) -> None:
    """Remove a session home without ever deleting through the store link.

    Idempotent; works when the link is missing or broken.
    """
    path = Path(path)
    try:
        _remove_link(path / ".gemini" / "antigravity-cli")
    except OSError as exc:
        logger.warning("Could not unlink agy store link in {}: {}", path, exc)
        return  # could not unlink safely: never rmtree through a link
    shutil.rmtree(path, ignore_errors=True)


def pid_alive(pid: int) -> bool:
    """True if process ``pid`` exists (no new dependencies; never raises)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_access_denied = 5
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        handle = k32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # Exists but not ours to query (other user / protected) -> alive.
            return ctypes.get_last_error() == error_access_denied
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # cannot tell: never sweep on doubt
            return code.value == still_active
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_owner_pid(session_home: Path) -> None:
    """Record the owning process in ``<session home>/owner.pid``."""
    _write_text(Path(session_home) / OWNER_FILE, f"{os.getpid()}\n")


def _read_owner_pid(session_home: Path) -> int | None:
    try:
        return int((session_home / OWNER_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def sweep_all_stale_session_homes(*, keep: Path | None = None) -> list[Path]:
    """:func:`sweep_stale_session_homes` for every spec under the agy root."""
    try:
        specs = [d.name for d in lightfall_agy_home().iterdir()
                 if d.is_dir() and not d.is_symlink() and not _is_junction(d)]
    except OSError:
        return []
    removed: list[Path] = []
    for name in sorted(specs):
        removed += sweep_stale_session_homes(name, keep=keep)
    return removed


def sweep_stale_session_homes(spec_name: str | None, *, keep: Path | None = None) -> list[Path]:
    """Remove this spec's session homes left behind by dead processes.

    A home is stale when its ``owner.pid`` names a process that is not alive, or
    when it has no readable ``owner.pid`` and is older than ``STALE_UNOWNED_S``
    (a young unowned home may be another process's connect in progress).
    Removal only ever goes through :func:`remove_session_home`.
    """
    sessions = spec_home(spec_name) / "sessions"
    try:
        children = list(sessions.iterdir())
    except OSError:
        return []
    keep_norm = os.path.normcase(os.path.abspath(keep)) if keep is not None else None
    removed: list[Path] = []
    now = time.time()
    for child in children:
        if keep_norm is not None and os.path.normcase(os.path.abspath(child)) == keep_norm:
            continue
        # Only plain directories: never follow a link that sits in sessions/.
        if child.is_symlink() or _is_junction(child) or not child.is_dir():
            continue
        pid = _read_owner_pid(child)
        if pid is not None:
            stale = not pid_alive(pid)
        else:
            try:
                stale = now - child.stat().st_mtime > STALE_UNOWNED_S
            except OSError:
                continue
        if stale:
            remove_session_home(child)
            removed.append(child)
    return removed


def _check_key(key: str) -> None:
    if not _KEY_RE.match(key):
        raise ValueError(f"invalid MCP server key {key!r} (allowed: A-Za-z0-9_.-)")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _union(existing: Any, new: list[str]) -> list[str]:
    out = [x for x in existing if isinstance(x, str)] if isinstance(existing, list) else []
    out += [x for x in new if x not in out]
    return out


def write_spec_settings(
    spec_home: Path,
    *,
    workspace: str,
    server_keys: list[str],
    project: str = DEFAULT_PROJECT,
    location: str = "global",
) -> Path:
    """Write/merge the shared per-spec ``settings.json`` atomically.

    trustedWorkspaces and allow entries are unioned with what is already there;
    deny is fixed; gcp project/location are overwritten.  Headless agy refuses
    anything not on ``allow`` before the hook runs, so the built-in categories are
    allowed here and the hook does the real gating.
    """
    for k in server_keys:
        _check_key(k)
    path = Path(spec_home) / ".gemini" / "antigravity-cli" / "settings.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    old_perm = old.get("permissions")
    if not isinstance(old_perm, dict):
        old_perm = {}
    settings = {
        "gcp": {"project": project, "location": location},
        "trustedWorkspaces": _union(old.get("trustedWorkspaces"), [os.path.normpath(workspace)]),
        "permissions": {
            "allow": _union(old_perm.get("allow"),
                            [f"mcp({PLUGIN_NAME}_{k}/*)" for k in server_keys] + list(BUILTIN_ALLOW)),
            "deny": list(BUILTIN_DENY),
        },
    }
    _atomic_write_text(path, json.dumps(settings, indent=1, ensure_ascii=False) + "\n")
    return path


@dataclass
class AgyHomeConfig:
    """Plugin content for one session home.

    ``port`` and ``token`` are required: agy does not expand env vars in
    ``mcp_config.json``, so they are written literally.  The token lives on disk
    only as long as the session home; ``remove_session_home`` deletes it.
    """

    servers: list[str]
    external_servers: dict[str, dict]
    system_prompt: str
    hook_command: str
    port: int
    token: str
    skills_src: Path | None = None
    subagents: list[SubagentSpec] = field(default_factory=list)
    # Model ids `agy models` lists; a subagent model outside these and
    # AGY_MODEL_ALIASES becomes ``inherit``.
    model_ids: frozenset[str] = frozenset()


def _subagent_model(spec: SubagentSpec, model_ids: Iterable[str]) -> str:
    model = (spec.model or "").strip()
    if model and (model in AGY_MODEL_ALIASES or model in model_ids):
        return model
    if model:
        # Claude names (sonnet/haiku/opus, claude-*) and unknown ids land here.
        logger.debug("agy subagent '{}': model {!r} is not an agy model; using inherit",
                     spec.name, model)
    return "inherit"


def _write_subagents(agents_dir: Path, cfg: AgyHomeConfig) -> None:
    """Write ``<name>.md`` per subagent; agy uses the file's ``name`` as TypeName.

    ``SubagentSpec.effort`` and ``.memory`` have no agy agent-file equivalent (no
    known ``effort`` key, no per-agent memory), so they are ignored.
    """
    taken = {"operator"}  # casefolded: Windows file names are case-insensitive
    for spec in cfg.subagents:
        name = spec.name
        if not _KEY_RE.fullmatch(name) or name in (".", ".."):
            logger.warning("agy: skipping subagent {!r}: invalid name (allowed: A-Za-z0-9_.-)",
                           name)
            continue
        if name.casefold() == "operator":
            logger.warning("agy: skipping subagent {!r}: the name is reserved for the operator",
                           name)
            continue
        if name.casefold() in taken:
            logger.warning("agy: skipping subagent {!r}: duplicate name", name)
            continue
        taken.add(name.casefold())
        description = (spec.description or "").strip() or name  # agy rejects an empty one
        # JSON strings are valid YAML double-quoted scalars, safe for any text.
        front = _SUBAGENT_FRONTMATTER.format(
            name=json.dumps(name),
            description=json.dumps(description, ensure_ascii=False),
            model=_subagent_model(spec, cfg.model_ids),
        )
        _write_text(agents_dir / f"{name}.md", front + "\n" + spec.prompt + "\n")


def write_session_config(session_home: Path, cfg: AgyHomeConfig) -> Path:
    """Write ONLY ``<session>/.gemini/config/...``; return the plugin directory."""
    for k in [*cfg.servers, *cfg.external_servers]:
        _check_key(k)
    clash = set(cfg.servers) & set(cfg.external_servers)
    if clash:
        raise ValueError(f"external server key collides with a tool server: {sorted(clash)}")
    config_dir = Path(session_home) / ".gemini" / "config"
    plugin = config_dir / "plugins" / PLUGIN_NAME

    # What `agy plugin enable lightfall` writes.
    _write_json(config_dir / "config.json", {"plugins": {PLUGIN_NAME: {"enabled": True}}})
    _write_json(
        plugin / "plugin.json",
        {"name": PLUGIN_NAME, "description": "Lightfall session tools, rules and skills."},
    )
    servers: dict[str, dict] = {
        key: {
            "type": "http",
            "url": f"http://127.0.0.1:{cfg.port}/mcp/{key}/",
            "headers": {"Authorization": f"Bearer {cfg.token}"},
        }
        for key in cfg.servers
    }
    servers.update(cfg.external_servers)
    _write_json(plugin / "mcp_config.json", {"mcpServers": servers})
    _write_json(
        plugin / "hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {"type": "command", "command": cfg.hook_command, "timeout": HOOK_TIMEOUT_S}
                        ],
                    }
                ]
            }
        },
    )
    _write_text(plugin / "rules" / "AGENTS.md", cfg.system_prompt)
    # agents/ holds exactly operator.md plus this session's subagents.
    agents = plugin / "agents"
    if agents.exists():
        shutil.rmtree(agents)
    _write_text(agents / "operator.md", _OPERATOR_FRONTMATTER + "\n" + cfg.system_prompt + "\n")
    _write_subagents(agents, cfg)

    skills = plugin / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    skills.mkdir(parents=True)
    if cfg.skills_src is not None and Path(cfg.skills_src).is_dir():
        for d in sorted(Path(cfg.skills_src).iterdir()):
            if d.is_dir():
                shutil.copytree(d, skills / d.name, dirs_exist_ok=True)
    return plugin
