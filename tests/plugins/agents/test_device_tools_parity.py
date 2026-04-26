"""Parity test: migrated device_tools agent matches old plugin's tools."""
from __future__ import annotations


def test_tool_names_match_legacy():
    from lucid.plugins.agents.device_tools import DeviceToolsAgent
    from lucid.plugins.tools.device_tools import DeviceToolPlugin

    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in DeviceToolsAgent().create_tools())
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in DeviceToolPlugin().create_tools())
    assert new_names == old_names
    assert len(new_names) == 9


def test_metadata_preserved():
    from lucid.plugins.agents.device_tools import DeviceToolsAgent
    p = DeviceToolsAgent()
    assert p.name == "device_tools"
    assert p.category == "devices"


def test_get_system_prompt_empty():
    """Pure-tools plugin contributes no skill prompt."""
    from lucid.plugins.agents.device_tools import DeviceToolsAgent
    assert DeviceToolsAgent().get_system_prompt() == ""
