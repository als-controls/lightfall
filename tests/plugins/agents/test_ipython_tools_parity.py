"""Parity test: migrated ipython_tools agent matches old plugin's tools."""
from __future__ import annotations


def test_tool_names_match_legacy():
    from lucid.plugins.agents.ipython_tools import IPythonToolsAgent
    from lucid.plugins.tools.ipython_tools import IPythonToolPlugin

    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in IPythonToolsAgent().create_tools())
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in IPythonToolPlugin().create_tools())
    assert new_names == old_names
    assert len(new_names) == 5


def test_metadata_preserved():
    from lucid.plugins.agents.ipython_tools import IPythonToolsAgent

    p = IPythonToolsAgent()
    assert p.name == "ipython_tools"
    assert p.category == "scripting"


def test_get_system_prompt_empty():
    """Pure-tools plugin contributes no skill prompt."""
    from lucid.plugins.agents.ipython_tools import IPythonToolsAgent
    assert IPythonToolsAgent().get_system_prompt() == ""
