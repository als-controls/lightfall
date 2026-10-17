"""assemble_spec_options collects each plugin's tools/external servers once."""
from __future__ import annotations

from pathlib import Path

from lightfall.agents.assembly import assemble_spec_options
from lightfall.agents.spec import AgentSpec
from lightfall.agents.tooling import tool
from lightfall.plugins.tool_plugin import ToolPlugin


@tool("ping", "Ping", {})
async def _ping(args):
    return {"content": []}


class _CountingPlugin(ToolPlugin):
    def __init__(self):
        self.tools_calls = 0
        self.external_calls = 0

    @property
    def name(self) -> str:
        return "counting"

    @property
    def description(self) -> str:
        return "Counts collection calls"

    def create_tools(self):
        self.tools_calls += 1
        return [_ping]

    def create_external_servers(self):
        self.external_calls += 1
        return {"counting_ext": {"type": "stdio", "command": "python", "args": []}}


class _ToolRegistry:
    def __init__(self, plugins):
        self._plugins = plugins

    def enabled_plugins(self):
        return list(self._plugins)


class _SpecRegistry:
    def enabled_specs(self):
        return []


def test_plugin_collection_runs_once(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("lightfall.agents.assembly.template_variables",
                        lambda: {"user": "ron", "beamline": "", "endstation": ""})
    plugin = _CountingPlugin()
    spec = AgentSpec(name="lightfall", description="d", prompt="p", scope="core",
                     source_path=Path("lightfall.md"), tools=("counting",))

    options = assemble_spec_options(spec, _ToolRegistry([plugin]), _SpecRegistry(), tmp_path)

    assert plugin.tools_calls == 1
    assert plugin.external_calls == 1
    assert [s.name for s in options["tool_servers"]] == ["counting"]
    assert set(options["external_servers"]) == {"counting_ext"}
    assert options["allowed_tools"] == ["mcp__counting__ping", "mcp__counting_ext__*"]
    assert "mcp_servers" not in options
