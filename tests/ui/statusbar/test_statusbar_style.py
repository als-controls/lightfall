"""Style rules for in-repo status bar plugins (static checks over their source).

Every module in ``src/lightfall/ui/statusbar/plugins`` is checked, so a new
plugin is covered as soon as it is added:

- it displays through ``set_state(text, icon=..., role=...)``;
- labels contain no unicode symbols/emoji (use a qtawesome ``icon=`` instead);
- no hex/CSS color literals (use a theme ``role=``);
- no low-level styling (``set_color``, ``set_icon``, ``qtawesome``,
  ``colors_changed``); the base class owns icons, colors and theme changes.

If a plugin really needs an exception, add it to ``ALLOWED`` with a reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from lightfall.plugins.statusbar_plugin import icon_like_chars

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "src" / "lightfall" / "ui" / "statusbar" / "plugins"
MODULES = sorted(p for p in PLUGIN_DIR.glob("*.py") if p.name != "__init__.py")

#: (module file name, rule) -> reason. Keep this empty if at all possible.
ALLOWED: dict[tuple[str, str], str] = {}

LOW_LEVEL_CALLS = {"set_color", "set_icon"}
HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$|color:\s*#[0-9a-fA-F]")


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _label_args(call: ast.Call) -> list[ast.AST]:
    args = list(call.args[:1])
    args += [kw.value for kw in call.keywords if kw.arg == "text"]
    return args


def violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    uses_set_state = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "set_state":
                uses_set_state = True
            if attr in {"set_state", "set_text"}:
                for arg in _label_args(node):
                    for text in _strings(arg):
                        if icon_like_chars(text):
                            found.append(f"glyph: line {node.lineno}: label {text!r} uses "
                                         f"{icon_like_chars(text)}; use icon=")
            if attr in LOW_LEVEL_CALLS:
                found.append(f"low-level: line {node.lineno}: {attr}(); use set_state()")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any(n.split(".")[0] == "qtawesome" for n in names):
                found.append(f"low-level: line {node.lineno}: imports qtawesome; "
                             "pass the icon name to set_state(icon=...)")
        if isinstance(node, ast.Attribute) and node.attr == "colors_changed":
            found.append(f"low-level: line {node.lineno}: colors_changed; the base class "
                         "re-renders on theme changes (override on_theme_changed)")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if HEX_COLOR.search(node.value):
                found.append(f"hex-color: line {node.lineno}: {node.value!r}; use a theme role")
    if not uses_set_state:
        found.append("set_state: never calls set_state()")
    return [v for v in found if (path.name, v.split(":")[0]) not in ALLOWED]


def test_plugins_found():
    assert len(MODULES) >= 10


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.stem)
def test_statusbar_plugin_style(path: Path):
    assert violations(path) == []


def test_checker_catches_old_style(tmp_path):
    bad = tmp_path / "bad_status.py"
    bad.write_text(
        "import qtawesome as qta\n"
        "class P:\n"
        "    def update(self):\n"
        "        self.set_text(f'\\u23f3 {3} tasks')\n"
        "        self.set_icon(qta.icon('mdi6.x'))\n"
        "        self.set_color('#e74c3c')\n"
        "        self._theme.colors_changed.connect(self.update)\n",
        encoding="utf-8")
    rules = {v.split(":")[0] for v in violations(bad)}
    assert rules == {"glyph", "low-level", "hex-color", "set_state"}
