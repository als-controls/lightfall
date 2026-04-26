"""Panel builder skill plugin with MCP tools.

Provides MCP tools for Claude to create and manage user-defined plugins,
particularly panel plugins. Includes tools for creating, listing, reloading,
and unloading user plugins.
"""

from __future__ import annotations

import re
from typing import Any

from lucid.plugins.agent_plugin import AgentPlugin
from lucid.utils.logging import logger


class PanelBuilderAgent(AgentPlugin):
    """Skill for building LUCID plugins via MCP tools.

    This skill provides Claude with tools to:
    - Create user plugins (persistent or temporary)
    - List user plugins with their status
    - Reload plugins after external edits
    - Unload plugins
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "panel_builder"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Panel Builder"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Tools for creating and managing user plugins"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "development"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 25

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for plugin building."""
        return '''
## Plugin Building Tools

You have access to tools for creating user plugins in LUCID.

### Plugin Kind is Determined by Class Hierarchy

The `plugin_type` parameter no longer exists. Instead, the kind of plugin is
inferred automatically from which base class your code subclasses:

- **`PanelPlugin`** — subclass this to register a new dock panel
- **`AgentPlugin`** — subclass this to extend the embedded Claude agent
  (adds skill prompts and MCP tools)

`__init_subclass__` auto-registers each concrete subclass; no explicit
`Registry.get_instance().register()` call is required.

### Creating a Plugin

Use `ncs_create_user_plugin` to write a plugin file to ~/lucid/plugins/.
The plugin will be validated (syntax + exec + concrete-PluginType-subclass
check), written to disk, and loaded immediately.

Example workflow:
1. User asks for a panel with specific functionality
2. You generate the panel code subclassing `PanelPlugin`
3. You call `ncs_create_user_plugin` with the code
4. The plugin is validated, written to disk, and loaded
5. User can open the panel from View > User > [Panel Name]

### Quick Prototyping

For rapid prototyping, use `ncs_create_temp_plugin` to create a temporary
plugin that will be lost on application restart. This is useful for testing
ideas before committing to a persistent plugin.

### Plugin Management

- `ncs_list_user_plugins`: See all loaded user plugins and their status
- `ncs_reload_plugin`: Force reload after external edits
- `ncs_unload_plugin`: Remove a plugin from the registry
'''

    def _validate_plugin_code(
        self,
        code: str,
        name: str,
    ) -> tuple[bool, str | None, list[str]]:
        """Validate user plugin code. Returns (is_valid, error, found_kinds).

        found_kinds is a list of type_names ("panel", "agent", ...) for
        each concrete PluginType subclass discovered.

        Performs in-memory validation:
        1. Syntax check via compile()
        2. Execution in isolated namespace to check imports
        3. Discovers concrete PluginType subclasses (kind inferred from hierarchy)
        4. Warning for dangerous imports
        """
        # 1. Syntax check
        try:
            compile(code, f"{name}.py", "exec")
        except SyntaxError as e:
            return False, f"Syntax error at line {e.lineno}: {e.msg}", []

        # 2. Execute in isolated namespace to check for import errors
        namespace: dict[str, Any] = {"__name__": f"lucid_user_plugins.{name}"}
        try:
            exec(code, namespace)
        except Exception as e:  # noqa: BLE001
            return False, f"Import/exec error: {e}", []

        # 3. Find PluginType subclasses (concrete only)
        from lucid.plugins.types import PluginType

        found_kinds: list[str] = []
        for v in namespace.values():
            if (
                isinstance(v, type)
                and issubclass(v, PluginType)
                and v is not PluginType
                and not getattr(v, "__abstractmethods__", None)
            ):
                found_kinds.append(v.type_name)

        if not found_kinds:
            return False, "No concrete PluginType subclass found", []

        # 4. Warn about dangerous imports (but don't fail)
        dangerous_patterns = [
            (r"\bsubprocess\.", "subprocess module"),
            (r"\bos\.system\b", "os.system"),
            (r"\beval\s*\(", "eval()"),
            (r"\bexec\s*\(", "exec()"),
        ]
        warnings = []
        for pattern, description in dangerous_patterns:
            if re.search(pattern, code):
                warnings.append(description)

        if warnings:
            logger.warning(
                "Plugin '{}' uses potentially dangerous: {}",
                name,
                ", ".join(warnings),
            )

        return True, None, found_kinds

    def create_tools(self) -> list[Any]:
        """Create plugin management MCP tools.

        Returns:
            List of tool functions.
        """
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, panel builder tools disabled")
            return []

        @tool(
            name="ncs_create_user_plugin",
            description="""Create a LUCID user plugin from Python code.

The plugin will be written to ~/lucid/plugins/ and automatically loaded.
The kind of plugin is determined by the class hierarchy in `code`:

- Subclass `AgentPlugin` to extend the embedded Claude agent (adds skill prompts + MCP tools)
- Subclass `PanelPlugin` to register a new dock panel

`__init_subclass__` auto-registers each concrete subclass on module load;
no explicit `Registry.get_instance().register()` call is required.

Returns success status, file path, and the discovered plugin kind(s).

## Panel Plugin Template

```python
\"\"\"My panel description.\"\"\"
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QLabel, QPushButton, QLineEdit, QVBoxLayout, QHBoxLayout,
)
from lucid.plugins.panel_plugin import PanelPlugin
from lucid.ui.panels.base import PanelMetadata


class MyPanel(PanelPlugin):
    \"\"\"Panel description.\"\"\"

    panel_metadata = PanelMetadata(
        id="user.my_panel",
        name="My Panel",
        description="What this panel does",
        icon="mdi6.icon-name",
        category="User",
    )

    def _setup_ui(self) -> None:
        \"\"\"Build the panel UI. Use self._layout (inherited QVBoxLayout).\"\"\"
        # DO NOT create a new layout - use the inherited self._layout
        self._layout.setContentsMargins(10, 10, 10, 10)

        label = QLabel("Hello!")
        self._layout.addWidget(label)

        btn = QPushButton("Click me")
        btn.clicked.connect(self._on_click)
        self._layout.addWidget(btn)

        self._layout.addStretch()

    def _on_click(self) -> None:
        \"\"\"Handle button click.\"\"\"
        pass
```

## Common Mistakes to AVOID:
- DON'T use `qtpy` imports - use `PySide6` directly
- DON'T override `__init__` - override `_setup_ui()` instead
- DON'T create `QVBoxLayout(self)` - use inherited `self._layout`
- DON'T use `lucid.panels.base` - use `lucid.ui.panels.base`

## Accessing Devices:
```python
from lucid.devices.catalog import DeviceCatalog
catalog = DeviceCatalog.get_instance()
motors = catalog.get_devices_by_category("motor")  # Returns DeviceInfo list
```

## Running Bluesky Plans:
```python
import bluesky.plan_stubs as bps
from lucid.acquire import get_engine

def my_plan():
    yield from bps.mv(motor, 0)

engine = get_engine()
engine.submit(my_plan(), description="My plan")
```""",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (becomes filename, e.g., 'my_panel' -> my_panel.py). Must be a valid Python identifier.",
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python source code for the plugin file",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what the plugin does (for logging)",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "If true, overwrite existing plugin with same name. Default: false",
                        "default": False,
                    },
                },
                "required": ["name", "code", "description"],
            },
        )
        async def create_user_plugin(args: dict) -> dict[str, Any]:
            """Create a user plugin from Python code."""
            from lucid.plugins.user_plugins import UserPluginService

            name = args["name"]
            code = args["code"]
            description = args["description"]
            overwrite = args.get("overwrite", False)

            # Validate name is a valid Python identifier
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
                return {
                    "success": False,
                    "error": "Invalid plugin name. Must be a valid Python identifier "
                    "(letters, numbers, underscore; must start with letter or underscore).",
                }

            # Validate code in-memory before writing
            is_valid, error, kinds = self._validate_plugin_code(code, name)
            if not is_valid:
                return {
                    "success": False,
                    "error": error,
                }

            # Get plugins directory from UserPluginService
            try:
                service = UserPluginService.get_instance()
                plugins_dir = service.get_plugins_directory()
            except Exception as e:
                logger.error("Failed to get UserPluginService: {}", e)
                return {
                    "success": False,
                    "error": f"Failed to access user plugins service: {e}",
                }

            file_path = plugins_dir / f"{name}.py"

            # Check for existing file
            if file_path.exists() and not overwrite:
                return {
                    "success": False,
                    "error": f"Plugin '{name}' already exists. Set overwrite=true to replace it.",
                }

            # Write the file
            try:
                file_path.write_text(code, encoding="utf-8")
            except Exception as e:
                logger.error("Failed to write plugin file {}: {}", file_path, e)
                return {
                    "success": False,
                    "error": f"Failed to write plugin file: {e}",
                }

            # Verify file was actually written
            if not file_path.exists():
                return {
                    "success": False,
                    "error": f"File write appeared to succeed but file does not exist: {file_path}",
                }

            # Load the plugin
            try:
                success = service.load_plugin_from_file(file_path)
                if not success:
                    info = service.get_plugin_info(file_path)
                    error_msg = info.load_error if info and info.load_error else "Unknown load error"
                    return {
                        "success": False,
                        "error": f"Plugin file written but failed to load: {error_msg}",
                        "path": str(file_path),
                    }
            except Exception as e:
                logger.warning("Plugin file written but failed to load: {}", e)
                return {
                    "success": False,
                    "error": f"Plugin file written but failed to load: {e}",
                    "path": str(file_path),
                }

            logger.info(
                "Created user plugin '{}': {} (overwrite={})",
                name,
                description,
                overwrite,
            )

            return {
                "success": True,
                "message": f"Plugin '{name}' created successfully (kinds: {', '.join(sorted(set(kinds)))})",
                "path": str(file_path),
                "description": description,
            }

        @tool(
            name="ncs_create_temp_plugin",
            description="""Create a temporary plugin that won't persist across restarts.

Useful for quick prototyping. The plugin is loaded immediately but will
be lost when the application exits.

The kind of plugin is determined by the class hierarchy in `code`:
- Subclass `AgentPlugin` for agent extensions (skill prompts + MCP tools)
- Subclass `PanelPlugin` for dock panels

Returns success status, temporary file path, and the discovered plugin kind(s).

IMPORTANT: Use the same imports and patterns as ncs_create_user_plugin.
See that tool's description for the required template.""",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (becomes filename)",
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python source code for the plugin. The class hierarchy determines the plugin kind.",
                    },
                },
                "required": ["name", "code"],
            },
        )
        async def create_temp_plugin(args: dict) -> dict[str, Any]:
            """Create a temporary plugin."""
            from lucid.plugins.user_plugins import UserPluginService

            name = args["name"]
            code = args["code"]

            # Validate name
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
                return {
                    "success": False,
                    "error": "Invalid plugin name",
                }

            # Validate code
            is_valid, error, kinds = self._validate_plugin_code(code, name)
            if not is_valid:
                return {
                    "success": False,
                    "error": error,
                }

            try:
                service = UserPluginService.get_instance()
                file_path = service.create_temp_plugin(name, code)

                return {
                    "success": True,
                    "message": f"Temporary plugin '{name}' created (kinds: {', '.join(sorted(set(kinds)))})",
                    "path": str(file_path),
                    "is_temporary": True,
                }
            except Exception as e:
                logger.error("Failed to create temp plugin: {}", e)
                return {
                    "success": False,
                    "error": str(e),
                }

        @tool(
            name="ncs_list_user_plugins",
            description="""List all user plugins with their status.

Returns information about each loaded plugin including:
- File path
- Registration status (what it registered)
- Any load errors
- Whether it's temporary""",
            input_schema={
                "type": "object",
                "properties": {},
            },
        )
        async def list_user_plugins(args: dict) -> dict[str, Any]:
            """List all user plugins."""
            from lucid.plugins.user_plugins import UserPluginService

            try:
                service = UserPluginService.get_instance()
                data = service.get_introspection_data()

                return {
                    "success": True,
                    "plugins_dir": data["plugins_dir"],
                    "hot_reload_enabled": data["hot_reload_enabled"],
                    "plugins": data["plugins"],
                    "total_count": data["loaded_plugin_count"],
                    "temp_count": data["temp_plugin_count"],
                }
            except Exception as e:
                logger.error("Failed to list user plugins: {}", e)
                return {
                    "success": False,
                    "error": str(e),
                }

        @tool(
            name="ncs_reload_plugin",
            description="""Force reload a user plugin after external edits.

Unloads the plugin (unregistering all its components) and reloads it
from disk. Use this after editing a plugin file externally.

Note: Hot-reload may cause instability if panel instances are still open.""",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (filename without .py extension)",
                    },
                },
                "required": ["name"],
            },
        )
        async def reload_plugin(args: dict) -> dict[str, Any]:
            """Reload a user plugin."""
            from lucid.plugins.user_plugins import UserPluginService

            name = args["name"]

            try:
                service = UserPluginService.get_instance()
                plugins_dir = service.get_plugins_directory()
                file_path = plugins_dir / f"{name}.py"

                if not file_path.exists():
                    return {
                        "success": False,
                        "error": f"Plugin file not found: {file_path}",
                    }

                success = service.reload_plugin(file_path)

                if success:
                    return {
                        "success": True,
                        "message": f"Plugin '{name}' reloaded successfully",
                        "path": str(file_path),
                    }
                else:
                    info = service.get_plugin_info(file_path)
                    error = info.load_error if info else "Unknown error"
                    return {
                        "success": False,
                        "error": f"Failed to reload: {error}",
                    }
            except Exception as e:
                logger.error("Failed to reload plugin: {}", e)
                return {
                    "success": False,
                    "error": str(e),
                }

        @tool(
            name="ncs_unload_plugin",
            description="""Unload a user plugin from the registry.

Removes the plugin's registrations (panels, skills, tools) without
deleting the file. The plugin won't be active until reloaded.

Note: Open panel instances may become unstable.""",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (filename without .py extension)",
                    },
                },
                "required": ["name"],
            },
        )
        async def unload_plugin(args: dict) -> dict[str, Any]:
            """Unload a user plugin."""
            from lucid.plugins.user_plugins import UserPluginService

            name = args["name"]

            try:
                service = UserPluginService.get_instance()
                plugins_dir = service.get_plugins_directory()
                file_path = plugins_dir / f"{name}.py"

                success = service.unload_plugin(file_path)

                if success:
                    return {
                        "success": True,
                        "message": f"Plugin '{name}' unloaded",
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Plugin '{name}' was not loaded",
                    }
            except Exception as e:
                logger.error("Failed to unload plugin: {}", e)
                return {
                    "success": False,
                    "error": str(e),
                }

        return [
            create_user_plugin,
            create_temp_plugin,
            list_user_plugins,
            reload_plugin,
            unload_plugin,
        ]
