"""Guard against drift between the two dependency lists in pyproject.toml.

Briefcase installs from its own hand-maintained
[tool.briefcase.app.lightfall].requires list, not [project].dependencies.
Any runtime dependency missing from the briefcase list produces installers
that crash at startup (e.g. nats-py, imported unconditionally via main.py).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _canonical_name(requirement: str) -> str:
    """Extract the PEP 503 canonical package name from a requirement string.

    Strips extras, version specifiers, and environment markers:
    "httpx[socks]>=0.27" -> "httpx", "netifaces>=0.11; sys_platform ..." ->
    "netifaces".
    """
    name = re.split(r"[;\[<>=!~ ]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def test_briefcase_requires_covers_project_dependencies():
    with PYPROJECT.open("rb") as f:
        pyproject = tomllib.load(f)

    project_deps = {_canonical_name(dep) for dep in pyproject["project"]["dependencies"]}
    briefcase_requires = {
        _canonical_name(req)
        for req in pyproject["tool"]["briefcase"]["app"]["lightfall"]["requires"]
    }

    missing = sorted(project_deps - briefcase_requires)
    assert not missing, (
        "Runtime dependencies missing from [tool.briefcase.app.lightfall].requires "
        f"(briefcase installers will crash at startup): {missing}"
    )
