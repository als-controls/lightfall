"""Claude assistant panel subpackage.

Contains the ClaudePanel and supporting components for the
Claude AI assistant integration.
"""

from lucid.ui.panels.claude.device_tools import DeviceToolPlugin
from lucid.ui.panels.claude.skill_registry import SkillRegistry
from lucid.ui.panels.claude.tool_registry import MCPToolRegistry

# NCSCoreToolPlugin is imported directly where needed to avoid
# circular imports with NCSMainWindow

__all__ = ["MCPToolRegistry", "DeviceToolPlugin", "SkillRegistry"]
