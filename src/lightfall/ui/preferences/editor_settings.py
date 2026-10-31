"""External tools settings plugin for code navigation preferences.

This module contains the ExternalToolsSettingsPlugin that allows users to
configure which external code editor to use for "click to open" functionality
in the logging panel and other code navigation features.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Choice,
    FormValues,
    Info,
    Section,
    SettingField,
    Toggle,
)
from lightfall.utils.editor_launcher import EDITOR_PROTOCOLS, CodeEditor, is_editor_available

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

# Every key on this page is console-only, so everything is shown at "This console".
CONSOLE = frozenset({"local"})

EDITOR_OPTIONS = [
    (CodeEditor.VSCODE.value, "Visual Studio Code"),
    (CodeEditor.PYCHARM.value, "PyCharm"),
]


def _effective_editor(values: FormValues) -> CodeEditor:
    value = values.effective("code_editor", CodeEditor.VSCODE.value)
    try:
        return CodeEditor(value) if value else CodeEditor.VSCODE
    except ValueError:
        return CodeEditor.VSCODE


def _is_pycharm(values: FormValues) -> bool:
    return _effective_editor(values) == CodeEditor.PYCHARM


def _protocol_status(values: FormValues) -> str:
    """Whether the URL protocol handler of the effective editor is registered."""
    editor = _effective_editor(values)
    protocol = EDITOR_PROTOCOLS.get(editor, editor.value)
    if is_editor_available(editor):
        return f"✓ {protocol}:// protocol is registered"
    return f"⚠ {protocol}:// protocol not found"


class ExternalToolsSettingsPlugin(SettingsPlugin):
    """Settings plugin for external tools configuration.

    Allows users to select which external code editor to use when clicking on
    code locations in the logging panel and other code navigation features:
    - VSCode: Uses vscode://file/{path}:{line}:{column} protocol
    - PyCharm: Uses jetbrains://pycharm/navigate/reference?path={path}&line={line}
      (requires JetBrains Toolbox to register the jetbrains:// protocol)

    Note: Both editors must be installed and have their URL protocol handlers
    registered for the "open in editor" functionality to work.
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "external_tools"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "External Tools"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "general"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 15  # Between appearance (0) and devices (20)

    def sections(self) -> Sequence[Section]:
        return [
            Section(
                "Code Editor",
                [
                    SettingField(
                        "code_editor",
                        Choice(EDITOR_OPTIONS),
                        label="Preferred editor",
                        description=(
                            "Select which editor to open when double-clicking on code "
                            "locations in the Logging panel. The editor will open the "
                            "file at the exact line number."
                        ),
                    ),
                    Info(_protocol_status, scopes=CONSOLE),
                ],
            ),
            Section(
                "URL Protocols",
                [
                    SettingField(
                        "suppress_pycharm_warning",
                        Toggle(),
                        label="Suppress JetBrains Toolbox warning on startup",
                        description=(
                            "The editor must be installed and have its URL protocol "
                            "handler registered. VSCode registers its protocol "
                            "automatically (vscode://file/{path}:{line}:{column}). "
                            "PyCharm requires JetBrains Toolbox to be installed; Toolbox "
                            "registers the jetbrains:// protocol handler "
                            "(jetbrains://pycharm/navigate/reference"
                            "?project={project}&path={path}:{line}:{column})."
                        ),
                        enabled_when=_is_pycharm,
                    ),
                ],
            ),
        ]
