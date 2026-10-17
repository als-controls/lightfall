"""User-visible strings say "Assistant" or the backend's name, never a hard-coded "Claude".

Scans string literals (AST) in UI modules; docstrings and logger.* arguments
are exempt. Claude-backend-only modules are not scanned.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import lightfall

ROOT = Path(lightfall.__file__).parent
FILES = [
    "claude/widget.py",
    "ui/panels/claude_panel.py",
    "ui/panels/claude/agent_session_tab.py",
    "ui/widgets/skill_trigger_button.py",
    "ui/preferences/tool_settings.py",
    "claude/widgets/question_request.py",
    "logbook/fragment_widgets.py",
    "ui/tutorial.py",
    "core/application.py",
    "agents/bus_tools.py",
]
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
    for rel in FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree) | _logger_arg_ids(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in skip and WORD.search(node.value)):
                hits.append(f"{rel}:{node.lineno}: {node.value[:60]!r}")
    assert hits == [], "\n".join(hits)
