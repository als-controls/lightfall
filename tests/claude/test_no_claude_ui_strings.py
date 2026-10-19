"""User-visible strings say "Assistant" or the backend's name, never a hard-coded "Claude".

Scans string literals (AST) in every module of the package, so new UI modules
are covered without editing a list; docstrings and logger.* arguments are
exempt. Modules whose "Claude" strings are legitimately Claude-specific are
listed in EXEMPT with the reason.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import lightfall

ROOT = Path(lightfall.__file__).parent
EXEMPT = {
    "agents/backends/claude_code/backend.py": "Claude Code backend's own errors",
    "agents/backends/info.py": "ClaudeCodeInfo.display_name",
    "claude/agent.py": "Claude-backend-only branches",
    "claude/lightfall_core_tools.py": "agent-facing tool errors, not UI",
    "plugins/agents/panel_builder.py": "agent-facing tool description",
    "setup/bootstrap.py": "names the selected backend",
    "setup/pages/assistant.py": "names the selected backend",
    "ui/docking/theme.py": "stylesheet comment",
    "ui/preferences/agent_backend_section.py": "\"Claude Code\" backend choice",
    "ui/preferences/claude_settings.py": "Claude-only settings controls",
}
WORD = re.compile(r"\bClaude\b")


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _logger_arg_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            for sub in ast.walk(node):
                ids.add(id(sub))
    return ids


def test_no_hardcoded_claude_in_ui_strings():
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree) | _logger_arg_ids(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in skip and WORD.search(node.value)):
                hits.append(f"{rel}:{node.lineno}: {node.value[:60]!r}")
    assert hits == [], "\n".join(hits)


def test_legacy_system_prompt_is_backend_neutral():
    """QT_SYSTEM_PROMPT may drive any backend: no claim that the model is Claude."""
    from lightfall.claude.agent import QT_SYSTEM_PROMPT

    assert not WORD.search(QT_SYSTEM_PROMPT)
    assert "Agent tool" not in QT_SYSTEM_PROMPT and "Task tool" not in QT_SYSTEM_PROMPT
    assert "lightfall_wait_for_idle" in QT_SYSTEM_PROMPT


def test_exemptions_still_exist():
    missing = [rel for rel in EXEMPT if not (ROOT / rel).is_file()]
    assert missing == []
