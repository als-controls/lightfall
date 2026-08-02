"""Agent definition files: parse markdown + YAML frontmatter into AgentSpec."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lightfall.utils.logging import logger

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_TEMPLATE_RE = re.compile(r"\{\{(\w+)\}\}")
_KNOWN_TOP = {"name", "description", "model", "effort", "tools", "skills", "memory", "lightfall"}
_KNOWN_LF = {"subagent", "on_message", "forward_min_severity"}
_ON_MESSAGE_VALUES = {"auto", "queue"}
_SEVERITIES = {"info", "warn", "critical"}


class AgentSpecError(ValueError):
    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(f"{path}: {message}" if path else message)
        self.path = path


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    prompt: str
    scope: str
    source_path: Path
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model: str | None = None
    effort: str | None = None
    memory: bool = True
    subagent: bool = True
    on_message: str = "queue"
    forward_min_severity: str | None = None


def _str_tuple(value: object, key: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise AgentSpecError(f"'{key}' must be a list of strings", path)
    return tuple(value)


def parse_agent_file(path: Path, scope: str) -> AgentSpec:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise AgentSpecError("missing YAML frontmatter block", path)
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise AgentSpecError(f"invalid YAML frontmatter: {e}", path) from e
    if not isinstance(meta, dict):
        raise AgentSpecError("frontmatter must be a mapping", path)

    for key in meta.keys() - _KNOWN_TOP:
        logger.warning("agent file {}: unknown frontmatter key '{}' ignored", path, key)

    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not name.strip():
        raise AgentSpecError("frontmatter 'name' is required", path)
    if not isinstance(description, str) or not description.strip():
        raise AgentSpecError("frontmatter 'description' is required", path)
    body = m.group(2).strip()
    if not body:
        raise AgentSpecError("agent prompt body is empty", path)

    lf = meta.get("lightfall") or {}
    if not isinstance(lf, dict):
        raise AgentSpecError("'lightfall' must be a mapping", path)
    for key in lf.keys() - _KNOWN_LF:
        logger.warning("agent file {}: unknown lightfall key '{}' ignored", path, key)

    on_message = lf.get("on_message", "queue")
    if on_message not in _ON_MESSAGE_VALUES:
        raise AgentSpecError(f"lightfall.on_message must be one of {sorted(_ON_MESSAGE_VALUES)}", path)
    fwd = lf.get("forward_min_severity")
    if fwd is not None and fwd not in _SEVERITIES:
        raise AgentSpecError(f"lightfall.forward_min_severity must be one of {sorted(_SEVERITIES)}", path)

    return AgentSpec(
        name=name.strip(),
        description=description.strip(),
        prompt=body,
        scope=scope,
        source_path=path,
        tools=_str_tuple(meta.get("tools"), "tools", path),
        skills=_str_tuple(meta.get("skills"), "skills", path),
        model=meta.get("model"),
        effort=meta.get("effort"),
        memory=bool(meta.get("memory", True)),
        subagent=bool(lf.get("subagent", True)),
        on_message=on_message,
        forward_min_severity=fwd,
    )


def resolve_template(prompt: str, variables: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            raise AgentSpecError(f"unknown template variable '{{{{{key}}}}}'")
        return variables[key]

    return _TEMPLATE_RE.sub(sub, prompt)
