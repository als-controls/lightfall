"""Skill settings plugin for NCS (DEPRECATED).

This module is deprecated. Use tool_settings.py instead.

The SkillSettingsPlugin has been replaced by ClaudeToolsSettingsPlugin
which manages both tool plugins and skills in a unified settings UI.

This module is kept for backward compatibility only.
"""

from __future__ import annotations

# Re-export from new module for backward compatibility
from lucid.ui.preferences.tool_settings import (
    ClaudeToolsSettingsPlugin,
    ToolPluginTableModel,
)

# Backward compatibility aliases
SkillSettingsPlugin = ClaudeToolsSettingsPlugin
SkillTableModel = ToolPluginTableModel

__all__ = [
    "SkillSettingsPlugin",
    "SkillTableModel",
    "ClaudeToolsSettingsPlugin",
    "ToolPluginTableModel",
]
