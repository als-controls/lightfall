"""MCP tool plugins for LUCID Claude assistant.

This package contains MCP tool plugins that provide Claude with
the ability to interact with various NCS subsystems.

Tool plugins in this package:
- device_tools: Device interaction (read, set, move, stop)
- ncs_tools: Panel and application interaction
- plan_tools: User plan creation and management
- ipython_tools: IPython console code execution
"""

from lucid.plugins.tools.device_tools import DeviceToolPlugin
from lucid.plugins.tools.ipython_tools import IPythonToolPlugin
from lucid.plugins.tools.plan_tools import PlanToolPlugin

# NCSCoreToolPlugin requires main_window, imported directly where needed

__all__ = ["DeviceToolPlugin", "IPythonToolPlugin", "PlanToolPlugin"]
