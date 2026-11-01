# tests/agents/test_app_wiring.py — spec resolution end-to-end
from lightfall.agents import builtin_agents_dir, user_agents_dir  # noqa: F401
from lightfall.agents.registry import AgentSpecRegistry


def test_bootstrap_registers_core_and_user(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    AgentSpecRegistry.reset_instance()
    from lightfall.main import _setup_agent_specs  # new function, called from app startup
    _setup_agent_specs()
    reg = AgentSpecRegistry.get_instance()
    assert reg.get("lightfall") is not None
    assert reg.get("observer") is not None
    AgentSpecRegistry.reset_instance()


def _synced_calls(monkeypatch):
    """Record what _on_content_synced hands the agent registry."""
    from lightfall.content import sources

    sentinel = [object()]
    requested, received = [], []
    monkeypatch.setattr(
        sources, "default_synced_items", lambda type_: requested.append(type_) or sentinel
    )

    class FakeRegistry:
        def set_synced(self, items):
            received.append(items)

    monkeypatch.setattr(
        AgentSpecRegistry, "get_instance", classmethod(lambda cls: FakeRegistry())
    )
    return sentinel, requested, received


def test_content_sync_with_agent_changes_refreshes_synced_agents(monkeypatch):
    from types import SimpleNamespace

    from lightfall.ui.mainwindow import LFMainWindow

    sentinel, requested, received = _synced_calls(monkeypatch)
    result = SimpleNamespace(changed_types=frozenset({"agent", "plan"}))
    LFMainWindow._on_content_synced(None, result)
    assert requested == ["agent"]
    assert len(received) == 1 and received[0] is sentinel


def test_content_sync_without_agent_changes_leaves_agents_alone(monkeypatch):
    from types import SimpleNamespace

    from lightfall.ui.mainwindow import LFMainWindow

    _sentinel, requested, received = _synced_calls(monkeypatch)
    for types_ in ({"plan"}, {"plugin", "skill"}, set()):
        LFMainWindow._on_content_synced(None, SimpleNamespace(changed_types=frozenset(types_)))
    assert requested == [] and received == []
