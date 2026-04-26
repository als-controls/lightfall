"""Built-in AgentPlugin classes shipped with lucid.

Each plugin lives in its own module. References (markdown docs surfaced via
the SDK Skill tool's lazy loading) live in <name>/references/ alongside.

To add a new built-in agent:
1. Create lucid/plugins/agents/<name>.py defining a class extending AgentPlugin.
2. Add a PluginEntry(type_name="agent", name="<name>",
   import_path="lucid.plugins.agents.<name>:<ClassName>") to builtin_manifest.py.
"""
