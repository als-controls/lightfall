"""Generate the private agy home (settings, plugin, rules, agent, skills).

agy reads ``<home>/.gemini/...`` (HOME/USERPROFILE overridden for the child), so a
private home keeps Lightfall's configuration away from the user's real
``~/.gemini``.  Layout and enablement record were verified live against agy 1.3.5.

Live findings (Task 4, step 0) that shape this module:

* agy does NOT expand ``${VAR}`` (nor ``$VAR`` / ``${env:VAR}`` / ``%VAR%``) in a
  plugin ``mcp_config.json``; the server is never contacted.  Per-session port and
  token must therefore be written literally (``port``/``token`` arguments) into a
  per-session home.  Without them, ``${LF_AGY_PORT}``/``${LF_AGY_TOKEN}``
  placeholders are written (no secret on disk, but agy cannot use them).
* The ``permissions.allow`` server part is matched literally: ``mcp(lightfall_*/*)``
  does not allow anything, ``mcp(lightfall_<key>/*)`` does.  One entry per server key.
* ``agy plugin enable lightfall`` writes ``<home>/.gemini/config/config.json``
  as ``{"plugins": {"lightfall": {"enabled": true}}}``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLUGIN_NAME = "lightfall"
DEFAULT_PROJECT = "lbl-gemini-enterprise-access"
HOOK_TIMEOUT_S = 290

_OPERATOR_FRONTMATTER = (
    "---\n"
    "name: operator\n"
    "description: Lightfall beamline control assistant\n"
    "mainAgent: true\n"
    "subagent: false\n"
    "excludeDefaultComponents: true\n"
    "inheritCustomizations: true\n"
    "inheritMcp: true\n"
    "tools: []\n"
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


def agy_home_for(spec_name: str | None, session_id: str | None = None) -> Path:
    """Home dir for an AgentSpec (``lightfall`` for the main assistant), created.

    With ``session_id`` the home is per session (``<spec>/<session_id>``), required
    because the port/token must be written literally into ``mcp_config.json``.
    """
    p = lightfall_agy_home() / (spec_name or "lightfall")
    if session_id:
        p = p / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class AgyHomeConfig:
    home: Path
    workspace: str
    servers: list[str]
    external_servers: dict[str, dict]
    system_prompt: str
    hook_command: str
    project: str = DEFAULT_PROJECT
    location: str = "global"
    skills_src: Path | None = None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def write_agy_home(
    cfg: AgyHomeConfig, port: int | None = None, token: str | None = None
) -> Path:
    """Write the agy home files (overwriting) and return the plugin directory.

    ``port``/``token`` are written literally into ``mcp_config.json`` when given
    (agy does not expand env vars there).  When omitted, ``${LF_AGY_PORT}`` /
    ``${LF_AGY_TOKEN}`` placeholders are written and no secret touches the disk.
    """
    gemini = cfg.home / ".gemini"
    plugin = gemini / "config" / "plugins" / PLUGIN_NAME

    keys = list(cfg.servers) + [k for k in cfg.external_servers if k not in cfg.servers]
    _write_json(
        gemini / "antigravity-cli" / "settings.json",
        {
            "gcp": {"project": cfg.project, "location": cfg.location},
            "trustedWorkspaces": [os.path.normpath(cfg.workspace)],
            "permissions": {
                "allow": [f"mcp({PLUGIN_NAME}_{k}/*)" for k in keys],
                "deny": ["command(*)", "write_file(*)", "read_url(*)", "execute_url(*)"],
            },
        },
    )
    # What `agy plugin enable lightfall` writes.
    _write_json(
        gemini / "config" / "config.json", {"plugins": {PLUGIN_NAME: {"enabled": True}}}
    )
    _write_json(
        plugin / "plugin.json",
        {"name": PLUGIN_NAME, "description": "Lightfall session tools, rules and skills."},
    )

    port_s = str(port) if port is not None else "${LF_AGY_PORT}"
    token_s = token if token is not None else "${LF_AGY_TOKEN}"
    servers: dict[str, dict] = {
        key: {
            "type": "http",
            "url": f"http://127.0.0.1:{port_s}/mcp/{key}/",
            "headers": {"Authorization": f"Bearer {token_s}"},
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
                            {
                                "type": "command",
                                "command": cfg.hook_command,
                                "timeout": HOOK_TIMEOUT_S,
                            }
                        ],
                    }
                ]
            }
        },
    )

    _write_text(plugin / "rules" / "AGENTS.md", cfg.system_prompt)
    _write_text(
        plugin / "agents" / "operator.md",
        _OPERATOR_FRONTMATTER + "\n" + cfg.system_prompt + "\n",
    )

    skills = plugin / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    skills.mkdir(parents=True)
    if cfg.skills_src is not None and Path(cfg.skills_src).is_dir():
        for d in sorted(Path(cfg.skills_src).iterdir()):
            if d.is_dir():
                shutil.copytree(d, skills / d.name, dirs_exist_ok=True)
    return plugin
