"""IPython console MCP tools for Claude assistant.

Provides tools for executing Python code in the embedded IPython console:
- Execute arbitrary Python code with output capture
- Push variables to the kernel namespace
- Clear console output
- Reset the kernel
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from lucid.plugins.mcp_tool import MCPToolPlugin
from lucid.plugins.agents._mcp_helpers import mcp_result
from lucid.utils.logging import logger


class IPythonToolPlugin(MCPToolPlugin):
    """MCP tools for interacting with the IPython console.

    This plugin provides tools that allow Claude to:
    - Execute Python code in the embedded IPython console
    - Push variables to the kernel namespace for user access
    - Manage the console state (clear, reset)

    The IPython console has access to `main_window` and `app` objects,
    making it useful for interactive scripting and exploration.

    Security Note:
        Code execution happens in the same process as the application.
        The user has authorized Claude to run code via the MCP tool system.
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "ipython_tools"

    @property
    def description(self) -> str:
        """Human-readable description for settings UI."""
        return "Execute Python code in the IPython console"

    @property
    def category(self) -> str:
        """Plugin category for settings UI."""
        return "scripting"

    def _get_main_window(self):
        """Get the main window instance.

        Returns:
            The NCSMainWindow instance or None if not available.
        """
        from lucid.core.application import NCSApplication

        app = NCSApplication.get_instance()
        return app.main_window if app else None

    def _get_ipython_panel(self):
        """Get the IPython panel instance.

        Returns:
            The IPythonPanel instance or None if not available.
        """
        window = self._get_main_window()
        if window is None:
            return None
        return window.get_panel("lucid.panels.ipython")

    def _ensure_kernel_ready(self) -> bool:
        """Ensure the IPython kernel is initialized.

        If the panel already exists, checks kernel readiness.
        If the panel doesn't exist, creates it via add_panel but then
        immediately hides it so the user's layout isn't disrupted.

        Returns:
            True if kernel is available, False otherwise.
        """
        panel = self._get_ipython_panel()
        if panel is not None:
            return panel._kernel_manager is not None

        # Panel doesn't exist yet — create it, then hide so it doesn't
        # pop up unexpectedly when Claude runs code.
        window = self._get_main_window()
        if window is None:
            return False

        panel = window.add_panel("lucid.panels.ipython")
        if panel is None:
            return False

        # Hide the panel so it doesn't visually pop up.
        # The kernel stays alive in the background.
        dm = getattr(window, "_docking_manager", None)
        if dm is not None:
            dm.hide_panel("lucid.panels.ipython")

        return panel._kernel_manager is not None

    def create_tools(self) -> list[Any]:
        """Create IPython MCP tools.

        Returns:
            List of tool functions.
        """
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, IPython tools disabled")
            return []

        @tool(
            name="ncs_ipython_execute",
            description=(
                "Execute Python code in the IPython console. "
                "The console has access to `main_window` and `app` objects. "
                "Returns the output (stdout/stderr) and any result value. "
                "Use this for interactive scripting, debugging, or exploring the application."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python code to execute. Can be multiple lines. "
                            "The code runs in the same namespace as the IPython console."
                        ),
                    },
                    "capture_output": {
                        "type": "boolean",
                        "description": (
                            "Whether to capture and return stdout/stderr. "
                            "Default is True. Set to False for code that produces "
                            "large output or interacts with Qt event loop."
                        ),
                        "default": True,
                    },
                },
                "required": ["code"],
            },
        )
        async def execute_code(args: dict) -> dict[str, Any]:
            """Execute Python code in the IPython console."""
            from lucid.claude._internal.threading import run_on_main_thread

            code = args["code"]
            capture_output = args.get("capture_output", True)

            def _execute():
                # Ensure panel is open
                if not self._ensure_kernel_ready():
                    return mcp_result({
                        "success": False,
                        "error": "IPython console not available. Install with: pip install qtconsole ipykernel",
                    })

                panel = self._get_ipython_panel()
                if panel is None or panel._kernel_manager is None:
                    return mcp_result({
                        "success": False,
                        "error": "IPython kernel not initialized",
                    })

                kernel = panel._kernel_manager.kernel
                shell = kernel.shell

                # Execute with optional output capture
                stdout_capture = io.StringIO()
                stderr_capture = io.StringIO()

                try:
                    if capture_output:
                        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                            result = shell.run_cell(code, store_history=True)
                    else:
                        result = shell.run_cell(code, store_history=True)

                    # Build response
                    response = {
                        "success": result.success,
                        "execution_count": result.execution_count,
                    }

                    if capture_output:
                        stdout_val = stdout_capture.getvalue()
                        stderr_val = stderr_capture.getvalue()
                        if stdout_val:
                            response["stdout"] = stdout_val
                        if stderr_val:
                            response["stderr"] = stderr_val

                    # Include result if there was one (not None)
                    if result.result is not None:
                        try:
                            response["result"] = repr(result.result)
                        except Exception:
                            response["result"] = "<unprintable result>"

                    # Include error info if execution failed
                    if not result.success and result.error_in_exec is not None:
                        response["error"] = str(result.error_in_exec)
                        response["error_type"] = type(result.error_in_exec).__name__

                    return mcp_result(response)

                except Exception as e:
                    logger.error("Error executing code in IPython: {}", e)
                    return mcp_result({
                        "success": False,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    })

            return run_on_main_thread(_execute)

        @tool(
            name="ncs_ipython_push_variable",
            description=(
                "Push a variable to the IPython console namespace. "
                "The variable will be available for the user to use interactively. "
                "Useful for sharing data or objects you've created with the user."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Variable name (must be a valid Python identifier)",
                    },
                    "value_code": {
                        "type": "string",
                        "description": (
                            "Python expression that evaluates to the value to push. "
                            "This is evaluated in the kernel namespace."
                        ),
                    },
                },
                "required": ["name", "value_code"],
            },
        )
        async def push_variable(args: dict) -> dict[str, Any]:
            """Push a variable to the IPython namespace."""
            from lucid.claude._internal.threading import run_on_main_thread

            name = args["name"]
            value_code = args["value_code"]

            def _push():
                # Validate variable name
                if not name.isidentifier():
                    return mcp_result({
                        "success": False,
                        "error": f"'{name}' is not a valid Python identifier",
                    })

                # Ensure panel is open
                if not self._ensure_kernel_ready():
                    return mcp_result({
                        "success": False,
                        "error": "IPython console not available",
                    })

                panel = self._get_ipython_panel()
                if panel is None or panel._kernel_manager is None:
                    return mcp_result({
                        "success": False,
                        "error": "IPython kernel not initialized",
                    })

                kernel = panel._kernel_manager.kernel
                shell = kernel.shell

                try:
                    # Evaluate the value expression
                    value = shell.user_ns_hidden.get("__builtins__", {})
                    value = eval(value_code, shell.user_global_ns, shell.user_ns)

                    # Push to namespace
                    shell.push({name: value})

                    return mcp_result({
                        "success": True,
                        "variable": name,
                        "value_repr": repr(value)[:200],  # Truncate long reprs
                    })
                except Exception as e:
                    logger.error("Error pushing variable to IPython: {}", e)
                    return mcp_result({
                        "success": False,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    })

            return run_on_main_thread(_push)

        @tool(
            name="ncs_ipython_get_namespace",
            description=(
                "Get information about variables in the IPython namespace. "
                "Useful for understanding what's available to work with."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "filter_prefix": {
                        "type": "string",
                        "description": (
                            "Only include variables starting with this prefix. "
                            "Leave empty for all user variables."
                        ),
                        "default": "",
                    },
                    "include_private": {
                        "type": "boolean",
                        "description": "Include variables starting with underscore",
                        "default": False,
                    },
                },
            },
        )
        async def get_namespace(args: dict) -> dict[str, Any]:
            """Get IPython namespace information."""
            from lucid.claude._internal.threading import run_on_main_thread

            filter_prefix = args.get("filter_prefix", "")
            include_private = args.get("include_private", False)

            def _get_ns():
                panel = self._get_ipython_panel()
                if panel is None or panel._kernel_manager is None:
                    return mcp_result({
                        "success": False,
                        "error": "IPython console not available or not initialized",
                    })

                kernel = panel._kernel_manager.kernel
                shell = kernel.shell

                # Get user namespace (excluding internal IPython stuff)
                user_ns = shell.user_ns
                internal_keys = set(shell.user_ns_hidden.keys())

                variables = {}
                for name, value in user_ns.items():
                    # Skip internal names
                    if name in internal_keys:
                        continue
                    # Skip private unless requested
                    if not include_private and name.startswith("_"):
                        continue
                    # Apply prefix filter
                    if filter_prefix and not name.startswith(filter_prefix):
                        continue

                    try:
                        type_name = type(value).__name__
                        value_repr = repr(value)[:100]  # Truncate
                    except Exception:
                        type_name = "<unknown>"
                        value_repr = "<unprintable>"

                    variables[name] = {
                        "type": type_name,
                        "repr": value_repr,
                    }

                return mcp_result({
                    "success": True,
                    "variables": variables,
                    "count": len(variables),
                })

            return run_on_main_thread(_get_ns)

        @tool(
            name="ncs_ipython_clear",
            description="Clear the IPython console output display.",
            input_schema={
                "type": "object",
                "properties": {},
            },
        )
        async def clear_console(args: dict) -> dict[str, Any]:
            """Clear the IPython console."""
            from lucid.claude._internal.threading import run_on_main_thread

            def _clear():
                panel = self._get_ipython_panel()
                if panel is None:
                    return mcp_result({
                        "success": False,
                        "error": "IPython console not available",
                    })

                panel.action_clear()
                return mcp_result({"success": True})

            return run_on_main_thread(_clear)

        @tool(
            name="ncs_ipython_reset",
            description=(
                "Reset the IPython kernel, clearing all variables and state. "
                "The initial namespace (main_window, app) will be restored."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        )
        async def reset_kernel(args: dict) -> dict[str, Any]:
            """Reset the IPython kernel."""
            from lucid.claude._internal.threading import run_on_main_thread

            def _reset():
                panel = self._get_ipython_panel()
                if panel is None:
                    return mcp_result({
                        "success": False,
                        "error": "IPython console not available",
                    })

                panel.action_reset_kernel()
                return mcp_result({
                    "success": True,
                    "message": "Kernel reset. Initial namespace restored.",
                })

            return run_on_main_thread(_reset)

        return [
            execute_code,
            push_variable,
            get_namespace,
            clear_console,
            reset_kernel,
        ]
