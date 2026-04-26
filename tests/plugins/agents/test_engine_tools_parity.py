"""Parity test: migrated engine_tools agent matches old plugin's tools."""
from __future__ import annotations


def test_tool_names_match_legacy():
    from lucid.plugins.agents.engine_tools import EngineToolsAgent
    from lucid.plugins.tools.engine_tools import EngineToolPlugin

    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in EngineToolsAgent().create_tools())
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in EngineToolPlugin().create_tools())
    assert new_names == old_names
    assert len(new_names) == 7


def test_metadata_preserved():
    from lucid.plugins.agents.engine_tools import EngineToolsAgent

    p = EngineToolsAgent()
    assert p.name == "engine_tools"
    assert p.category == "acquisition"


def test_get_system_prompt_empty():
    """Pure-tools plugin contributes no skill prompt."""
    from lucid.plugins.agents.engine_tools import EngineToolsAgent
    assert EngineToolsAgent().get_system_prompt() == ""
