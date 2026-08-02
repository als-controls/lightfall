"""Built-in ToolPlugin classes shipped with lightfall.

Each plugin lives in its own module. References (markdown docs surfaced via
the SDK Skill tool's lazy loading) live in <name>/references/ alongside.

To add a new built-in agent:
1. Create lightfall/plugins/agents/<name>.py defining a class extending ToolPlugin.
2. Add a PluginEntry(type_name="tool", name="<name>",
   import_path="lightfall.plugins.agents.<name>:<ClassName>") to builtin_manifest.py.
"""
