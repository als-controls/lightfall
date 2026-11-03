"""Claude Code (claude_agent_sdk) backend."""
from lightfall.agents.backends.claude_code.backend import ClaudeCodeBackend
from lightfall.agents.backends.claude_code.no_window import patch_sdk_no_console_window
from lightfall.agents.backends.claude_code.tools import to_sdk_server, to_sdk_tool

# No console window per agent session under the console-less lightfall-gui.exe.
patch_sdk_no_console_window()

__all__ = ["ClaudeCodeBackend", "to_sdk_server", "to_sdk_tool"]
