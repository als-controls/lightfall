# tests/agents/test_import_boundary.py
"""claude_agent_sdk may only be imported by the Claude backend + legacy shims."""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "lightfall"
ALLOWED = {
    "agents/backends/claude_code",          # the backend itself (prefix)
    "claude/agent.py",                      # ClaudeSDKClient + Windows cmdline patch
    "claude/_session_assembly.py",          # create_sdk_mcp_server monkeypatch point
    "agents/assembly.py",                   # subagent_definitions() test-compat wrapper
    "monitor/advisor.py",                   # guarded type aliases for collect_reply (unit-tested there)
}
PATTERN = re.compile(r"^\s*(from|import)\s+claude_agent_sdk\b", re.MULTILINE)


def _allowed(rel: str) -> bool:
    return any(rel == a or rel.startswith(a + "/") for a in ALLOWED)


def test_only_backend_imports_claude_sdk():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if PATTERN.search(path.read_text(encoding="utf-8")) and not _allowed(rel):
            offenders.append(rel)
    assert offenders == [], f"claude_agent_sdk imported outside the backend: {offenders}"
