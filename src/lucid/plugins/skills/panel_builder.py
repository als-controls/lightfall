"""Panel builder skill plugin with MCP tools.

Provides MCP tools for Claude to create and manage user-defined plugins,
particularly panel plugins. Includes tools for creating, listing, reloading,
and unloading user plugins.
"""

from __future__ import annotations

import re
from typing import Any

from lucid.plugins.skill_plugin import SkillPlugin
from lucid.utils.logging import logger


class PanelBuilderSkill(SkillPlugin):
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

### Creating a Panel Plugin

Use `ncs_create_user_plugin` to create a panel plugin file in ~/lucid/plugins/.
The plugin will be automatically loaded and available in the View menu.

Example workflow:
1. User asks for a panel with specific functionality
2. You generate the panel code using the panel_design skill knowledge
3. You call ncs_create_user_plugin with the code
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
        plugin_type: str | None = None,
    ) -> tuple[bool, str | None]:
        """Validate plugin code without writing to disk.

        Performs in-memory validation:
        1. Syntax check via compile()
        2. Execution in isolated namespace to check imports
        3. Type-specific checks (e.g., panel_metadata for panels)
        4. Warning for dangerous imports

        Args:
            code: Python source code for the plugin.
            name: Plugin name (for error messages).
            plugin_type: Optional type hint ("panel", "skill", etc.).

        Returns:
            Tuple of (is_valid, error_message). error_message is None if valid.
        """
        # 1. Syntax check
        try:
            compile(code, f"{name}.py", "exec")
        except SyntaxError as e:
            return False, f"Syntax error at line {e.lineno}: {e.msg}"

        # 2. Execute in isolated namespace to check for import errors
        namespace: dict[str, Any] = {"__name__": f"lucid_user_plugins.{name}"}
        try:
            exec(code, namespace)
        except Exception as e:
            return False, f"Execution error: {type(e).__name__}: {e}"

        # 3. Type-specific validation
        if plugin_type == "panel":
            # Check for a class with panel_metadata
            panel_classes = [
                v
                for v in namespace.values()
                if isinstance(v, type) and hasattr(v, "panel_metadata")
            ]
            if not panel_classes:
                return False, (
                    "No panel class found. Ensure your class has a "
                    "'panel_metadata' class attribute of type PanelMetadata."
                )

            # Check for self-registration
            if "PanelRegistry" not in code:
                return False, (
                    "Missing self-registration. Add at the end of your file:\n"
                    "PanelRegistry.get_instance().register(YourPanel, replace=True)"
                )

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

        return True, None

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
Supports panel plugins, skill plugins, and MCP tool plugins.

The code must include self-registration. For panels:
```python
PanelRegistry.get_instance().register(MyPanel, replace=True)
```

Returns success status and file path.""",
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
                    "plugin_type": {
                        "type": "string",
                        "description": "Optional type hint for validation: 'panel', 'skill', 'mcp_tool'",
                        "enum": ["panel", "skill", "mcp_tool"],
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
            plugin_type = args.get("plugin_type")

            # Validate name is a valid Python identifier
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
                return {
                    "success": False,
                    "error": "Invalid plugin name. Must be a valid Python identifier "
                    "(letters, numbers, underscore; must start with letter or underscore).",
                }

            # Validate code in-memory before writing
            is_valid, error = self._validate_plugin_code(code, name, plugin_type)
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

            # Load the plugin
            try:
                success = service.load_plugin_from_file(file_path)
                if not success:
                    info = service.get_plugin_info(file_path)
                    if info and info.load_error:
                        return {
                            "success": False,
                            "error": f"Plugin file written but failed to load: {info.load_error}",
                            "path": str(file_path),
                        }
            except Exception as e:
                logger.warning("Plugin file written but failed to load: {}", e)

            logger.info(
                "Created user plugin '{}': {} (overwrite={})",
                name,
                description,
                overwrite,
            )

            return {
                "success": True,
                "message": f"Plugin '{name}' created successfully",
                "path": str(file_path),
                "description": description,
            }

        @tool(
            name="ncs_create_temp_plugin",
            description="""Create a temporary plugin that won't persist across restarts.

Useful for quick prototyping. The plugin is loaded immediately but will
be lost when the application exits.

Returns success status and temporary file path.""",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (becomes filename)",
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python source code for the plugin",
                    },
                    "plugin_type": {
                        "type": "string",
                        "description": "Optional type hint for validation",
                        "enum": ["panel", "skill", "mcp_tool"],
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
            plugin_type = args.get("plugin_type")

            # Validate name
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
                return {
                    "success": False,
                    "error": "Invalid plugin name",
                }

            # Validate code
            is_valid, error = self._validate_plugin_code(code, name, plugin_type)
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
                    "message": f"Temporary plugin '{name}' created",
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
