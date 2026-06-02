"""
lightfall.claude - Qt/PySide6 integration with Claude Agent SDK (part of Lightfall).

This package enables Claude AI to interact with Qt applications through:
- Visual understanding (screenshots)
- Widget hierarchy introspection
- Programmatic widget interaction

Authentication:
    Two authentication methods are supported:

    1. API Key (pay-per-use):
       - Set ANTHROPIC_API_KEY environment variable, or
       - Pass api_key parameter to QtClaudeAgent/ClaudeAssistantWidget

    2. OAuth (Claude Pro/Max subscription):
       - Run `claude login` in terminal to authenticate
       - No API key needed after login - uses stored OAuth credentials

Example (high-level widget):
    ```python
    from PySide6.QtWidgets import QApplication, QMainWindow
    from lightfall.claude import ClaudeAssistantWidget

    app = QApplication([])
    window = QMainWindow()
    claude = ClaudeAssistantWidget(target_window=window)
    claude.show()
    app.exec()
    ```

Example (low-level API):
    ```python
    from lightfall.claude import QtClaudeAgent

    agent = QtClaudeAgent(target_window=window)
    agent.message_received.connect(lambda msg: print(f"Claude: {msg}"))
    agent.query_sync("What widgets are in this window?")
    ```
"""

from lightfall.claude.agent import QtClaudeAgent
from lightfall.claude.permission_manager import PermissionManager
from lightfall.claude.widget import ClaudeAssistantWidget
from lightfall.claude.widgets.permission_request import PermissionRequestWidget

__all__ = [
    "QtClaudeAgent",
    "ClaudeAssistantWidget",
    "PermissionManager",
    "PermissionRequestWidget",
]
