"""Verify the builtin manifest exposes 10 agent entries (no skill/mcp_tool entries)."""
from __future__ import annotations


def test_manifest_has_10_agent_entries_and_no_skill_or_mcp_tool_entries():
    from lucid.plugins.builtin_manifest import builtin_manifest

    type_counts: dict[str, int] = {}
    for entry in builtin_manifest.plugins:
        type_counts.setdefault(entry.type_name, 0)
        type_counts[entry.type_name] += 1

    assert type_counts.get("agent") == 10
    assert type_counts.get("skill", 0) == 0
    assert type_counts.get("mcp_tool", 0) == 0


def test_manifest_lists_expected_agent_names():
    from lucid.plugins.builtin_manifest import builtin_manifest

    agent_names = {e.name for e in builtin_manifest.plugins if e.type_name == "agent"}
    assert agent_names == {
        "alignment", "plan_design", "scan_planning", "panel_design", "panel_builder",
        "device_tools", "plan_tools", "engine_tools", "ipython_tools",
        "ncs_core_tools",
    }
    # skill_docs is GONE
    assert "skill_docs" not in agent_names


def test_manifest_agent_import_paths_resolve():
    """Each agent's import_path must be importable and yield an AgentPlugin subclass."""
    import importlib

    from lucid.plugins.agent_plugin import AgentPlugin
    from lucid.plugins.builtin_manifest import builtin_manifest

    for entry in builtin_manifest.plugins:
        if entry.type_name != "agent":
            continue
        module_path, class_name = entry.import_path.split(":")
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        assert issubclass(cls, AgentPlugin), f"{entry.import_path} is not an AgentPlugin"
